"""Bounded Astronomy mesh connection diagnostic, separate from protocol4 scoring.

Run with explicit KUBECONFIG and KARTHOS_REPO. Only evaluation-owned deployments
are instrumented; original templates are restored with independent watchdog cover.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import json,time,traceback
import ipaddress
from urllib.parse import urlsplit
from pathlib import Path
from evaluation import runner as r,pilot
from evaluation.protocol import Cell
from evaluation.structured_capture import load_config
from evaluation.log_capture import PollingLogCapture
from evaluation.manifests import k6_job
from evaluation.generator_execution import execute_generator
from evaluation.verify import parse_time
from .packet_capture import PacketCapture


def verify_target(namespace,agent,gateway):
    if namespace.get('metadata',{}).get('labels',{}).get(r.OWNER_LABEL)!=r.OWNER_VALUE:
        raise ValueError('Astronomy namespace is not evaluation-owned')
    jobs=sorted(job['job_name'] for name,cfg in agent.get('receivers',{}).items()
                if name.startswith('prometheus') for job in cfg.get('config',{}).get('scrape_configs',[])
                if job.get('job_name','').startswith('mesh-'))
    if not jobs or 'service_graph' in gateway.get('connectors',{}):
        raise ValueError('explicit Istio source required; trace pairing cannot substitute')
    return jobs


IDLE_SCRIPT_NAME = 'eval-k6-idle-boundary'


def idle_script_configmap():
    return {'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':IDLE_SCRIPT_NAME,
            'namespace':'karthos-eval-astronomy','labels':{r.OWNER_LABEL:r.OWNER_VALUE}},
            'data':{'run.js':(r.ROOT/'evaluation/workloads/idle_boundary.js').read_text()}}


def idle_boundary_job(cell, run_id, target):
    endpoint=urlsplit(target)
    if endpoint.scheme!='http' or endpoint.port!=8080 or endpoint.path not in ('','/'):
        raise ValueError('idle diagnostic requires direct frontend-proxy PodIP HTTP8080')
    ipaddress.ip_address(endpoint.hostname)
    job=k6_job(cell.demo,cell.source,'idle-boundary',cell.namespace,r.CONFIG['k6_image'],target,
        duration_seconds=300,rate=2,workload='idle-boundary',artifact_mode=True,
        recovery_seconds=r.CONFIG['artifact_recovery_seconds'])
    spec=job['spec']['template']['spec']
    spec['volumes'][0]['configMap']['name']=IDLE_SCRIPT_NAME
    env=spec['containers'][0]['env']
    env[:]=[x for x in env if x['name'] not in ('RUN_ID','RATE')]
    env.append({'name':'RUN_ID','value':run_id})
    return job


def idle_window_bounds(markers,ledger):
    if not ledger:raise ValueError('idle diagnostic has no completed requests')
    end=max((row['completed_at'] for row in ledger),key=parse_time)
    start=markers[0]['started_at']
    return dict(start=start,end=end,actual_workload_end=markers[-1]['ended_at'],
                observation_seconds=(parse_time(end)-parse_time(start)).total_seconds(),
                window_semantics='closed21-pair diagnostic; end is last actual request completion, not300s fixed load')


def execute(root, mode='matched'):
    if mode not in ('matched','idle-boundary'):raise ValueError('unsupported packet diagnostic mode')
    root=Path(root);root.mkdir(parents=True,exist_ok=False)
    r._DEADLINE=time.monotonic()+1200
    r.CONFIG.update(protocol_version=4,instrumentation_budget_seconds=0)
    cell=Cell('astronomy','mesh',1)
    r.write(root/'harness-fingerprint-before.json',pilot.resume_fingerprint())
    r.write(root/'product-source-hash.json',dict(hash=r.repository_fingerprint(r.REPO)))
    r.write(root/'control-script.py',Path(__file__).read_text())
    report=dict(state='starting',not_a_formal_pilot=True,evidence_scope='Connection close diagnostic, not full resource accuracy',
                started_at=r.utc(),planned_duration_seconds=180,rate_journeys_per_second=2,
                capture_preroll_seconds=60,telemetry_drain_seconds=90)
    capture=None;logs=[];job=None;verified=False;planned_end=None;failure=None;idle_cm=None
    report['mode']=mode
    if mode=='idle-boundary':
        report.update(planned_duration_seconds=None,max_duration_seconds=300,rate_journeys_per_second=None,
                      offered_model='closed-per-vu-iterations',expected_pairs=21,expected_requests=42,
                      path_change='direct frontend-proxy PodIP8080 bypasses nginx observer; proxy to frontend upstream unchanged',
                      connection_reuse='verify from packets; implicit HTTP transport retries remain possible')
    try:
        r.check_ownership(False)
        namespace=r.kube_json('get','namespace',cell.namespace)
        configs={}
        for kind in ('agent','gateway'):
            cm=r.kube_json('get','configmap','karthos-'+kind+'-config','-n',r.KARTHOS_NAMESPACE)
            configs[kind]=load_config(next(v for k,v in cm['data'].items() if k.endswith(('.yaml','.yml'))))
        report['mesh_scrape_jobs']=verify_target(namespace,configs['agent'],configs['gateway'])
        r.write(root/'source-configs.json',configs)
        r.write(root/'before-pods.json',r.kube_json('get','pods','-n',cell.namespace))
        capture=PacketCapture(root/'packets',time.time()+1200,r.CONFIG['astronomy_locust_image'])
        capture.start()
        report['state']='packet_capture_ready';r.write(root/'probe-result.json',report)
        pods=r.kube_json('get','pods','-n',cell.namespace)
        for pod in pods['items']:
            name=pod['metadata']['name']
            is_source=name.startswith('otel-collector-agent-')
            is_front=any(pod['metadata']['uid']==p['metadata']['uid'] for p in capture.pods.values())
            if not is_source and not is_front:continue
            for container in pod['spec']['containers']:
                c=container['name']
                if c not in ('frontend','frontend-proxy','istio-proxy','opentelemetry-collector'):continue
                path=root/'logs'/(name+'__'+c+'.log')
                logs.append(PollingLogCapture(cell.namespace,name,c,path,r.utc(),max_bytes=256*1024*1024).start())
        runtime=r.kube('exec','-n',cell.namespace,'deployment/frontend','-c','frontend','--','/nodejs/bin/node','-p',
                       'JSON.stringify({node:process.version,keepAliveTimeoutEnv:process.env.KEEP_ALIVE_TIMEOUT||null})',check=False)
        r.write(root/'frontend-node-runtime.json',dict(returncode=runtime.returncode,stdout=runtime.stdout,stderr=runtime.stderr))
        if mode=='matched':
            pilot.install_scripts(cell)
        else:
            # Existing matched warmup fixture is reused without rewriting its CM.
            warmup_script=r.kube_json('get','configmap','eval-k6-script','-n',cell.namespace)
            if warmup_script.get('metadata',{}).get('labels',{}).get(r.OWNER_LABEL)!=r.OWNER_VALUE:
                raise ValueError('existing evaluation-owned warmup script is required')
            r.write(root/'warmup-script-before.json',warmup_script)
        from evaluation.semantics_pilot import run_warmup
        report['warmup']=run_warmup(cell,root,root.name,image_lock_root=root)
        pilot.sleep_bounded(60)
        path=root/'generator';path.mkdir()
        run_id=root.name+':'+cell.slug+':packet-diagnostic'
        if mode=='idle-boundary':
            pod_ip=str(ipaddress.ip_address(capture.pods['frontend-proxy']['status']['podIP']))
            target='http://'+('['+pod_ip+']' if ':' in pod_ip else pod_ip)+':8080'
            idle_cm=idle_script_configmap()
            idle_cm['metadata']['annotations']={'evaluation.karthos.dev/diagnostic-run':root.name}
            r.write(root/'idle-script-configmap.json',idle_cm)
            created=r.kube('create','-f','-','-o','json',input_text=json.dumps(idle_cm))
            idle_cm=json.loads(created.stdout)
            r.write(root/'idle-script-created.json',idle_cm)
            job=idle_boundary_job(cell,run_id,target)
            duration=300
            report['target']=target
            r.write(root/'idle-boundary-script.js',(r.ROOT/'evaluation/workloads/idle_boundary.js').read_text())
        else:
            job=k6_job(cell.demo,cell.source,'packet-diagnostic',cell.namespace,r.CONFIG['k6_image'],
                       'http://frontend-proxy:8080',duration_seconds=180,rate=2,workload='checkout-heavy',
                       artifact_mode=True,recovery_seconds=r.CONFIG['artifact_recovery_seconds'])
            env=job['spec']['template']['spec']['containers'][0]['env']
            env[:]=[x for x in env if x['name']!='RUN_ID']
            env.extend([dict(name='RUN_ID',value=run_id),dict(name='EVAL_CORRELATE_TRACES',value='1')])
            duration=180
        pilot.configure_generator_deadline(job,duration,r.CONFIG,r._DEADLINE-time.monotonic(),time.time())
        r.write(path/'job.json',job)
        report.update(state='generating',run_id=run_id);r.write(root/'probe-result.json',report)
        result=execute_generator(job,cell.namespace,path,run_id,'k6',mode,export_timeout_seconds=r.CONFIG['artifact_export_seconds'])
        verified=pilot.generator_log_complete(result)
        if not verified:raise RuntimeError('generator artifacts incomplete; retained for recovery')
        markers=json.loads((path/'generator-window.json').read_text())
        ledger=[json.loads(line) for line in (path/'ledger.ndjson').read_text().splitlines() if line.strip()]
        bounds=idle_window_bounds(markers,ledger) if mode=='idle-boundary' else pilot_window_bounds(markers,ledger)
        if mode=='idle-boundary':
            report['actual_pauses']=[{key:row.get(key) for key in ('pair_index','idle_target_seconds','fresh_connection_control','warm_completed_at','started_at','actual_idle_seconds')} for row in ledger if row.get('phase')=='probe']
            report['scheduling_evidence']='not applicable: closed21-pair model; no2rps or dropped-arrival accuracy claim'
        planned_end=parse_time(bounds['end']).timestamp()
        report.update(state='draining',window=bounds,requests=len(ledger),http_503=sum(x.get('status')==503 for x in ledger),
                      unexpected_outcomes=sum(x.get('outcome_ok') is not True for x in ledger),workload_exit_code=result['workload_exit_code'],
                      failures=[x for x in ledger if x.get('outcome_ok') is not True])
        r.write(root/'probe-result.json',report)
        pilot.sleep_bounded(90)
        report['state']='complete'
    except BaseException as exc:
        report.update(state='incomplete',error=str(exc),traceback=traceback.format_exc());failure=exc
    finally:
        r._DEADLINE=None
        if job:
            try:pilot.cleanup_generator(job['metadata']['name'],cell.namespace,root/'generator',verified)
            except Exception as exc:report['generator_cleanup_error']=str(exc)
        if idle_cm and verified:
            try:
                current=r.kube_json('get','configmap',IDLE_SCRIPT_NAME,'-n',cell.namespace)
                if (current['metadata'].get('labels',{}).get(r.OWNER_LABEL)!=r.OWNER_VALUE
                    or current['metadata'].get('annotations',{}).get('evaluation.karthos.dev/diagnostic-run')!=root.name
                    or ('uid' in idle_cm['metadata'] and current['metadata']['uid']!=idle_cm['metadata']['uid'])):
                    raise ValueError('idle script ownership or UID changed; preserving it')
                r.kube('delete','configmap',IDLE_SCRIPT_NAME,'-n',cell.namespace,'--wait=true','--timeout=60s')
            except Exception as exc:report['idle_script_cleanup_error']=str(exc)
        elif idle_cm:
            report['idle_script_retained_for_recovery']=True
        if logs:
            with ThreadPoolExecutor(max_workers=len(logs)) as pool:
                for outcome in pool.map(lambda stream:stop_log(stream),logs):
                    if outcome:report.setdefault('log_stop_errors',[]).append(outcome)
            r.write(root/'log-capture-status.json',[stream.status() for stream in logs])
        if capture:report['packet_capture']=capture.stop(planned_load_end_epoch=planned_end)
        report['finished_at']=r.utc()
        report['generator_artifacts_complete']=verified
        report['application_outcomes']=('fail' if report['unexpected_outcomes'] else 'pass') if 'unexpected_outcomes' in report else 'incomplete'
        report['packet_evidence_valid']=bool(report.get('packet_capture',{}).get('valid'))
        r.write(root/'probe-result.json',report)
        r.write(root/'after-pods.json',r.kube_json('get','pods','-n',cell.namespace))
        r.write(root/'harness-fingerprint-after.json',pilot.resume_fingerprint())
        r.checksums(root)
    if failure:raise failure
    return report


def pilot_window_bounds(markers,ledger):
    from evaluation.repair_pilot import window_bounds
    return window_bounds(markers[0]['started_at'],markers[-1]['ended_at'],180,ledger)


def stop_log(stream):
    try:stream.stop()
    except Exception as exc:return str(exc)
    return None


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=('matched','idle-boundary'),default='matched')
    parser.add_argument('--run-id',default=datetime.now(timezone.utc).strftime('astronomy-packet-diagnostic-%Y%m%dT%H%M%SZ'))
    args=parser.parse_args()
    if Path(args.run_id).name!=args.run_id or args.run_id in ('.','..'):raise ValueError('run ID must be one directory name')
    root=r.OUT/'repairs-v4-preparation-20261021'/args.run_id
    result=execute(root,mode=args.mode)
    print(json.dumps({key:result.get(key) for key in ('state','requests','http_503','unexpected_outcomes','packet_evidence_valid')}))

if __name__=='__main__':main()
