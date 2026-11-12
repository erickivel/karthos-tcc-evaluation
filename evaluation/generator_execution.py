"""Execute an artifact-producing generator and ACK only verified local evidence.

The held generator container keeps its files available until export is complete.
This helper never deletes cluster resources; the caller owns recovery and cleanup.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import runner as r
from .generator_artifacts import download_export, REMOTE, K6, LOCUST


class ArtifactExportError(RuntimeError):
    """Generator execution/export failed; its Job and Pod are retained."""


def _duration_seconds(container):
    duration=next((x.get('value','') for x in container.get('env',[]) if x.get('name')=='DURATION'),None)
    match=re.fullmatch(r'(\d+(?:\.\d+)?)(ms|s|m|h)?',duration or '')
    if not match:
        raise ValueError('generator DURATION must be an explicit numeric duration')
    return float(match[1])*{'ms':.001,'s':1,'m':60,'h':3600,None:1}[match[2]]


def _remaining(deadline):
    if r._DEADLINE is not None:
        deadline=min(deadline,r._DEADLINE)
    return deadline-time.monotonic()


def _budget(deadline,limit=20):
    remaining=_remaining(deadline)
    if remaining<=0:
        raise TimeoutError('generator execution or study deadline reached')
    return min(limit,remaining)


def _get_json(kind,namespace,deadline,*args):
    result=r.kube('get',kind,*args,'-n',namespace,'-o','json',
                  timeout=_budget(deadline),quiet=True)
    return json.loads(result.stdout)


def _pause(deadline):
    time.sleep(_budget(deadline,1))


def _terminated(pods,pod_name,container_name):
    for pod in pods.get('items',[]):
        if pod.get('metadata',{}).get('name')!=pod_name:
            continue
        for container in pod.get('status',{}).get('containerStatuses',[]):
            if container.get('name')==container_name:
                return container.get('state',{}).get('terminated')
    return None


def _materialize(raw,path,generator,mode):
    from .pilot import extract_events
    for name in {'generator.log','generator-version.txt'}|(K6 if generator=='k6' else LOCUST):
        shutil.copyfile(raw/name,path/name)
    log=(raw/'generator.log').read_text()
    for marker,filename in [('REQUEST','ledger.ndjson'),('ATTEMPT','attempts.ndjson')]:
        records=extract_events(log,marker) if mode in ('matched','native-ledger','idle-boundary','timing-probe') else []
        r.write(path/filename,''.join(json.dumps(record)+'\n' for record in records))
    r.write(path/'generator-window.json',extract_events(log,'WINDOW') if mode in ('matched','native-ledger','idle-boundary','timing-probe') else [])


def execute_generator(job,namespace,path,run_id,generator,mode,*,export_timeout_seconds=180)->dict:
    """Apply, await READY, export and materialize, ACK, then observe Job termination."""
    from .pilot import loggable_pod
    path=Path(path);path.mkdir(parents=True,exist_ok=True)
    name=job.get('metadata',{}).get('name')
    pod_name=None
    acknowledged=False
    def progress(phase,**details):
        record=dict(phase=phase,recorded_at=r.utc(),job=name,pod=pod_name,**details)
        r.write(path/'generator-execution-status.json',record)
        with (path/'generator-execution-progress.ndjson').open('a') as out:
            out.write(json.dumps(record)+'\n')
    try:
        container=job['spec']['template']['spec']['containers'][0]
        container_name=container['name']
        ready_deadline=time.monotonic()+_duration_seconds(container)+180
        _budget(ready_deadline)
        progress('applying')
        r.apply(job)
        r.write(path/'generator-job-status.json',_get_json('job',namespace,ready_deadline,name))
        startup_deadline=min(ready_deadline,time.monotonic()+120)
        while not pod_name:
            _budget(startup_deadline)
            inventory=_get_json('pods',namespace,startup_deadline,'-l','job-name='+name)
            r.write(path/'generator-pods-poll.json',inventory)
            pod_name=loggable_pod(inventory,container_name)
            if not pod_name:
                progress('waiting_for_container')
                _pause(startup_deadline)
        progress('waiting_for_ready',container=container_name)
        while True:
            _budget(ready_deadline)
            try:
                inventory=_get_json('pods',namespace,ready_deadline,'-l','job-name='+name)
                r.write(path/'generator-pods-poll.json',inventory)
                termination=_terminated(inventory,pod_name,container_name)
                if termination is not None:
                    raise ValueError('generator container terminated before READY: '+json.dumps(termination))
                probe=r.kube('exec','-n',namespace,pod_name,'-c',container_name,'--',
                             'test','-f',REMOTE+'/READY',check=False,
                             timeout=_budget(ready_deadline,10),quiet=True)
            except subprocess.TimeoutExpired as exc:
                progress('ready_probe_timeout',reason=str(exc))
                _pause(ready_deadline)
                continue
            if probe.returncode==0:
                r.write(path/'generator-pods.json',inventory)
                break
            _pause(ready_deadline)
        progress('ready')
        # Export has its own bounded transfer allowance, also capped by the study deadline.
        transfer_deadline=time.monotonic()+export_timeout_seconds
        timeout=_budget(transfer_deadline,export_timeout_seconds)
        progress('exporting',timeout_seconds=timeout)
        raw=path/'generator-artifacts'
        exported=download_export(namespace,pod_name,container_name,raw,generator,mode,run_id,
                                 timeout_seconds=timeout)
        if exported.get('verified') is not True or exported.get('semantic_complete') is not True:
            raise ValueError('download did not verify complete raw generator artifacts')
        _materialize(raw,path,generator,mode)
        r.write(path/'generator-artifact-status.json',exported)
        progress('materialized',workload_exit_code=exported.get('workload_exit_code'))
        # ACK follows checksum/semantic validation AND every required local derived file.
        ack_deadline=time.monotonic()+15
        response=r.kube('exec','-n',namespace,pod_name,'-c',container_name,'--',
                        'touch',REMOTE+'/ACK',timeout=_budget(ack_deadline,15),quiet=True)
        if response.returncode:
            raise ValueError('generator ACK failed: '+response.stderr)
        acknowledged=True
        progress('acknowledged')
    except (ValueError,KeyError,TypeError,OSError,RuntimeError,subprocess.TimeoutExpired) as exc:
        failed=dict(verified=False,semantic_complete=False,reason=str(exc),pod_name=pod_name,
                    job_name=name,acknowledged=acknowledged,recorded_at=r.utc())
        r.write(path/'generator-artifact-status.json',failed)
        progress('export_failed',reason=str(exc),acknowledged=acknowledged)
        raise ArtifactExportError(str(exc)) from exc

    job_deadline=time.monotonic()+45
    job_finished=False
    kubernetes_job_failed=None
    job_wait_error=None
    while _remaining(job_deadline)>0:
        try:
            document=_get_json('job',namespace,job_deadline,name)
            r.write(path/'generator-job-status.json',document)
            status=document.get('status',{})
            conditions=status.get('conditions',[])
            failed=bool(status.get('failed')) or any(c.get('type')=='Failed' and c.get('status')=='True' for c in conditions)
            succeeded=bool(status.get('succeeded')) or any(c.get('type')=='Complete' and c.get('status')=='True' for c in conditions)
            if failed or succeeded:
                job_finished=True;kubernetes_job_failed=failed
                break
            _pause(job_deadline)
        except (ValueError,OSError,RuntimeError,subprocess.TimeoutExpired) as exc:
            job_wait_error=str(exc)
            break
    if not job_finished and job_wait_error is None:
        job_wait_error='Job termination not observed within 45 seconds or study deadline'
    result=dict(exported,pod_name=pod_name,container_name=container_name,
                kubernetes_job_failed=kubernetes_job_failed,job_finished=job_finished,
                job_wait_error=job_wait_error,generator_pods=inventory,acknowledged=True)
    progress('complete' if job_finished else 'job_wait_incomplete',
             kubernetes_job_failed=kubernetes_job_failed,job_wait_error=job_wait_error)
    return result
