"""Protocol 3: four-cell, two-generator semantics pilot."""
from .protocol import cells_for_repeat

PROTOCOL_VERSION = 3


def schedule():
    result=[]
    for phase, duration in [('smoke',60),('measured',300)]:
        for index,cell in enumerate(cells_for_repeat(1)):
            order=('k6','locust') if index%2==0 else ('locust','k6')
            for generator in order:
                result.append(dict(phase=phase,cell=cell,spec=dict(
                    name=f'{generator}-checkout-heavy',generator=generator,
                    workload='checkout-heavy',duration_seconds=duration,rate=2,
                    protocol_version=PROTOCOL_VERSION)))
    return result


def quiet_pair(rows, interval, max_sample_age=60):
    if len(rows)<2:return False
    a,b=rows[-2:]
    if not a.get('values') or a['values']!=b.get('values'):return False
    if b['observed_at']-a['observed_at']<interval:return False
    return all(b.get('sample_times',{}).get(k,0)>a.get('sample_times',{}).get(k,0) and
               all(0 <= row['observed_at']-row.get('sample_times',{}).get(k,float('-inf')) <= max_sample_age
                   for row in (a,b)) for k in a['values'])


def evidence_valid(result, derivation_valid):
    return bool(result.get('oracle_valid') and result.get('source_valid') and derivation_valid)


def vm_query_request(expression, **params):
    return dict(query=expression,nocache='1',**params)


def export_parity(cell,path,start,end,seconds):
    """Use the exact locked product compiler, never a hand-reimplemented expression."""
    import json
    import urllib.parse
    from . import runner as r
    from .pilot import metric_queries
    from .protocol import source_id
    services=r.kube_json('get','services','-n',r.KARTHOS_NAMESPACE).get('items',[])
    vm=next(s['metadata']['name'] for s in services if 'victoria' in s['metadata']['name'])
    manifest=[]
    helper=r.ROOT/'evaluation/helpers/render_query.go'
    with r.forward('service/'+vm,r.KARTHOS_NAMESPACE,18428,8428) as base:
        for filename,route,params,key in metric_queries(start,end,seconds,source_id(cell.source)):
            if key!='samples':continue
            item=dict(api_file=filename,params=params,compiler_revision=r.cmd(
                ['git','rev-parse','HEAD'],cwd=r.REPO).stdout.strip())
            try:
                compiled=json.loads(r.cmd(['go','run',str(helper)],cwd=r.REPO,
                    input_text=json.dumps(params),timeout=120).stdout)
                item.update(compiled)
                request=vm_query_request(compiled['expression'],start=compiled['from'],end=compiled['to'],
                             step=str(compiled['step_seconds']))
                data=dict(request=request,response=r.get_json(base+'/api/v1/query_range?'+urllib.parse.urlencode(request)))
                points=compiled['evaluation_points']
                if len(points)>1:
                    from .verify import parse_time
                    if (parse_time(points[-1])-parse_time(points[-2])).total_seconds()!=compiled['step_seconds']:
                        extra=vm_query_request(compiled['expression'],time=points[-1])
                        data['final']=dict(request=extra,response=r.get_json(base+'/api/v1/query?'+urllib.parse.urlencode(extra)))
                r.write(path/'vm-parity'/filename,data)
            except Exception as exc:item['error']=str(exc)
            manifest.append(item)
    r.write(path/'vm-parity'/'manifest.json',dict(protocol_version=3,queries=manifest,
        raw_histogram_components='vm-raw.ndjson contains duration sum and count series'))


def counter_snapshot(rows, observed_at):
    import json
    import statistics
    values,timestamps,intervals={},{},[]
    for row in rows:
        pairs=sorted(zip(row.get('timestamps',[]),row.get('values',[])))
        if not pairs:continue
        identity=json.dumps(row['metric'],sort_keys=True,separators=(',',':'))
        timestamps[identity],values[identity]=pairs[-1][0]/1000,pairs[-1][1]
        intervals.extend((b[0]-a[0])/1000 for a,b in zip(pairs,pairs[1:]) if b[0]>a[0])
    return dict(observed_at=observed_at,values=values,sample_times=timestamps,
        measured_collection_interval_seconds=statistics.median(intervals) if intervals else None)


def quiet_counter(cell,path,phase):
    import json
    import time
    import urllib.parse
    from . import runner as r
    from .pilot import sleep_bounded
    from .protocol import source_id
    records=[]
    result=dict(stable=False,phase=phase,bound_seconds=90,records=records,
        boundary='source-partitioned stored counters; does not prove absence of in-flight requests')
    began=time.monotonic()
    try:
        services=r.kube_json('get','services','-n',r.KARTHOS_NAMESPACE).get('items',[])
        vm=next(s['metadata']['name'] for s in services if 'victoria' in s['metadata']['name'])
        checkout='checkoutservice' if cell.demo=='boutique' else 'checkout'
        selector='servicegraph_request_count{source="'+source_id(cell.source)+'",edge_server="'+checkout+'"}'
        with r.forward('service/'+vm,r.KARTHOS_NAMESPACE,18428,8428) as base:
            while time.monotonic()-began<90:
                stamp=time.time()
                params=urllib.parse.urlencode({'match[]':selector,'start':stamp-120,'end':stamp})
                with r.urllib.request.urlopen(base+'/api/v1/export?'+params,
                    timeout=r.http_timeout(max(0.1,min(10,90-(time.monotonic()-began))))) as response:
                    raw=response.read().decode()
                rows=[json.loads(line) for line in raw.splitlines() if line.strip()]
                snap=counter_snapshot(rows,stamp)
                snap['raw_rows']=rows
                records.append(snap)
                interval=snap['measured_collection_interval_seconds']
                if interval and quiet_pair(records,interval,r.CONFIG['thresholds']['max_freshness_seconds']):
                    result.update(stable=True,measured_collection_interval_seconds=interval);break
                remaining=90-(time.monotonic()-began)
                if remaining<=0:break
                sleep_bounded(min(interval or 5,remaining))
    except Exception as exc:result['error']=str(exc)
    result.update(elapsed_seconds=time.monotonic()-began,finished_at=r.utc())
    r.write(path/f'quiet-{phase}.json',result)
    return result


def validate_platform_images(images,tag):
    backends=[row for row in images if '/karthos-backend:' in row.get('image','')]
    if not backends:raise RuntimeError('deployed backend image missing from lock')
    for row in backends:
        if not row['image'].endswith(':'+tag) or 'sha256:' not in row.get('image_id',''):
            raise RuntimeError('deployed backend does not match immutable evaluation revision')


def derivation_evidence(path,source):
    import json
    file=path/'api-error-count.json'
    if not file.exists():return False
    samples=json.loads(file.read_text()).get('data',{}).get('samples',[])
    from .scoring import selected, derivation_contract
    selected_samples=selected([x for x in samples if x.get('labels',{}).get('source')==source])
    # Both workloads deliberately emit failed checkout calls; another edge
    # cannot satisfy the selected edge's derivation contract.
    contract=derivation_contract(selected_samples,source)
    return bool(selected_samples) and contract['state']=='pass'


def resolve_study_deadline(value, now, limit):
    from datetime import datetime,timedelta,timezone
    bound=now+timedelta(seconds=limit)
    if value:
        absolute=datetime.fromisoformat(value.replace('Z','+00:00'))
        if absolute.tzinfo is None:raise ValueError('--deadline-at requires an RFC3339 timezone')
        bound=min(bound,absolute)
    remaining=(bound-now).total_seconds()
    if remaining<=0:raise ValueError('study deadline has already expired')
    return remaining,bound.astimezone(timezone.utc).isoformat()


def run_warmup(cell,parent,study_id,*,image_lock_root=None):
    import json,time
    from . import runner as r
    from . import pilot
    from .manifests import k6_job
    from .generator_execution import execute_generator
    path=parent/'warmup'
    path.mkdir(parents=True,exist_ok=False)
    run_id=study_id+':'+cell.slug+':warmup'
    target='http://frontend:80' if cell.demo=='boutique' else 'http://frontend-proxy:8080'
    job=k6_job(cell.demo,cell.source,'warmup',cell.namespace,r.CONFIG['k6_image'],target,
        duration_seconds=20,rate=1,workload='checkout-heavy',artifact_mode=True,
        recovery_seconds=r.CONFIG['artifact_recovery_seconds'])
    env=job['spec']['template']['spec']['containers'][0]['env']
    env[:]=[x for x in env if x['name']!='RUN_ID']
    env.append(dict(name='RUN_ID',value=run_id))
    verified=False
    status=dict(run_id=run_id,state='starting',protocol_version=3,started_at=r.utc())
    r.write(path/'warmup-status.json',status)
    try:
        pilot.configure_generator_deadline(job,20,r.CONFIG,
            None if r._DEADLINE is None else r._DEADLINE-time.monotonic(),time.time())
        r.write(path/'job.json',job)
        result=execute_generator(job,cell.namespace,path,run_id,'k6','matched',
            export_timeout_seconds=r.CONFIG['artifact_export_seconds'])
        verified=pilot.generator_log_complete(result)
        if not verified:raise RuntimeError('warmup generator artifacts invalid')
        code=result['workload_exit_code']
        status.update(workload_exit_code=code,generator_artifacts_complete=True)
        if code not in (0,99):raise RuntimeError('warmup infrastructure/tool exit: '+str(code))
        ledger=[json.loads(line) for line in (path/'ledger.ndjson').read_text().splitlines() if line.strip()]
        pilot.lock_runtime_images(image_lock_root or parent.parent.parent,r.image_ids(result['generator_pods']))
        status.update(state='quality_failed' if code==99 else 'complete',
            requests=len(ledger),unexpected_outcomes=sum(not row.get('outcome_ok',False) for row in ledger))
        return status
    except BaseException as exc:
        status.update(state='incomplete',error=str(exc));raise
    finally:
        status.update(finished_at=r.utc(),generator_artifacts_complete=verified)
        r.write(path/'warmup-status.json',status)
        saved=r._DEADLINE;r._DEADLINE=time.monotonic()+40
        try:pilot.cleanup_generator(job['metadata']['name'],cell.namespace,path,verified)
        finally:
            r._DEADLINE=saved
            r.checksums(path)


def main(argv=None):
    import argparse
    import json
    import time
    from datetime import datetime,timezone
    from . import runner as r
    from . import pilot
    from .protocol import source_id
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id')
    parser.add_argument('--replace-existing',action='store_true')
    parser.add_argument('--smoke-only',action='store_true')
    parser.add_argument('--deadline-at',help='RFC3339 absolute deadline; may shorten but never extend eight-hour budget')
    args=parser.parse_args(argv)
    run_id=args.run_id or datetime.now(timezone.utc).strftime('semantics-v3-%Y%m%dT%H%M%SZ')
    from pathlib import Path
    if Path(run_id).name!=run_id or run_id in ('.','..'):raise ValueError('run ID must be one directory name')
    budget,deadline_at=resolve_study_deadline(args.deadline_at,datetime.now(timezone.utc),r.CONFIG['study_deadline_seconds'])
    root=r.OUT/run_id
    root.mkdir(parents=True,exist_ok=False)
    r._DEADLINE=time.monotonic()+budget
    report=dict(protocol_version=3,run_id=run_id,state='starting',started_at=r.utc(),
        expected_matched_windows=16,expected_native_windows=0,cells=[],deadline_at=deadline_at,
        planned_windows=[dict(phase=x['phase'],cell=x['cell'].slug,**x['spec']) for x in schedule()],
        error_policy_version='http5xx-grpc-nonzero-v1')
    r.write(root/'run-report.json',report)
    try:
        report['preflight']=r.preflight(args.replace_existing)
        report['pins']=r.stage()
        report['platform_image_tag']=pilot.select_platform_tag(report['pins'])
        r.write(root/'locked-config.json',r.CONFIG)
        r.write(root/'locked-harness.json',pilot.resume_fingerprint())
        r.shutil.copytree(r.ROOT/'evaluation',root/'locked-harness',ignore=r.shutil.ignore_patterns('__pycache__'))
        r.cmd([str(r.REPO/'test/e2e/prepare-platform-images.sh')],cwd=r.REPO,
            capture=root/'image-build.log',timeout=1800)
        report.update(state='running',images_prepared_at=r.utc())
        rows=schedule()
        for phase in ('smoke','measured'):
            if phase=='measured':
                calibration=[dict(cell=x['cell'],valid=x['evidence_valid'],windows=x['windows']) for x in report['cells']]
                r.write(root/'frozen-thresholds.json',pilot.freeze_thresholds(r.CONFIG['thresholds'],calibration))
                if args.smoke_only:report['state']='smoke_complete';break
            for cell in cells_for_repeat(1):
                pilot.assert_pins(report['pins'])
                specs=[x['spec'] for x in rows if x['phase']==phase and x['cell']==cell]
                report.update(active_cell=cell.slug,active_phase=phase)
                r.write(root/'run-report.json',report)
                status=pilot.run_cell(cell,root,smoke=phase=='smoke',replace_existing=args.replace_existing,
                    specs=specs,include_native=False,platform_tag=report['platform_image_tag'])
                path=root/phase/cell.slug
                valid=True
                for spec in specs:
                    window=path/'windows'/spec['name']
                    scored=json.loads((window/'verification.json').read_text())
                    derived=derivation_evidence(window,source_id(cell.source))
                    gate=evidence_valid(scored,derived)
                    r.write(window/'evidence-gate.json',dict(valid=gate,derivation_valid=derived,
                        oracle_valid=scored.get('oracle_valid'),source_valid=scored.get('source_valid')))
                    r.checksums(window)
                    valid=valid and gate
                report['cells'].append(dict(cell=cell.slug,phase=phase,evidence_valid=valid,windows=status['windows']))
                r.write(root/'run-report.json',report)
                if not valid:raise RuntimeError('invalid required evidence; see window evidence-gate.json')
        else:report['state']='complete'
    except BaseException as exc:
        report.update(state='incomplete',error=str(exc));raise
    finally:
        report.update(finished_at=r.utc(),attempts=r.attempt_statuses(root))
        report.pop('active_cell',None);report.pop('active_phase',None)
        r.write(root/'run-report.json',report)
        r.checksums(root)
        r._DEADLINE=None
        print('semantics pilot evidence:',root,flush=True)


if __name__=='__main__':main()
