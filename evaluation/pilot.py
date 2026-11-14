"""Protocol v2: independent per-window evidence and two matched generators."""
from __future__ import annotations

import hashlib
import json
import statistics
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

from . import runner as r
from .protocol import cells_for_repeat, source_id
from .observers import observer_config, observer_patch, service_patch
from .capture import ContinuousCapture


def windows(repeat: int, smoke: bool = False) -> list[dict]:
    generators = ('k6', 'locust') if repeat % 2 else ('locust', 'k6')
    return [dict(generator=g, workload=w, duration_seconds=(60 if smoke else d),
                 name=f'{g}-{w}', rate=(1 if smoke else 2))
            for g in generators for w, d in
            ([('checkout-heavy', 60)] if smoke else [('legacy', 180), ('checkout-heavy', 300)])]


def container_metrics(thesis=False):
    prefix='container' if thesis else 'k8s.container'
    return prefix+'.cpu.usage',prefix+'.memory.usage'


def metric_queries(start: str, end: str, seconds: int, source: str | None = None, *, thesis=False) -> list[tuple]:
    if not 60 <= seconds <= 3600:
        raise ValueError('public metric window must be between 60 and 3600 seconds')
    common = {'from': start, 'to': end}
    cpu,memory=container_metrics(thesis)
    queries = [
        ('api-nodes.json', '/api/v1/graph/nodes', {}, 'nodes'),
        ('api-edges.json', '/api/v1/graph/edges', {}, 'edges'),
        *[(f'api-{name}-raw.json', '/api/v1/metrics',
           dict(common, metric_name=metric, aggregation='latest'), 'samples')
          for name, metric in [('request', 'servicegraph.request.count'),
                               ('error', 'servicegraph.request.error.count')]],
        *[(f'api-{name}-count.json', '/api/v1/metrics',
           {'metric_name': metric, 'from': end, 'to': end,
            'aggregation': 'increase', 'window': f'{seconds}s'}, 'samples')
          for name, metric in [('request', 'servicegraph.request.count'),
                               ('error', 'servicegraph.request.error.count')]],
        ('api-duration.json', '/api/v1/metrics',
         {'metric_name': 'servicegraph.request.duration', 'from': end, 'to': end,
          'window': f'{seconds}s'}, 'samples'),
        *[(f'api-{name}.json', '/api/v1/metrics',
           dict(common, metric_name=metric), 'samples')
          for name, metric in [('cpu',cpu),('memory',memory)]],
    ]

    if source:
        for _, _, params, _ in queries:
            if params.get('metric_name', '').startswith('servicegraph.'):
                params['source'] = source
    return queries


def smoke_evidence_valid(windows: list[dict]) -> bool:
    return bool(windows) and all(w.get('oracle_valid') and w.get('source_valid') for w in windows)


def freeze_thresholds(thresholds: dict, calibration: list[dict]) -> dict:
    if len(calibration) < 4 or not all(x.get('valid') for x in calibration):
        raise ValueError('all four smoke cells must have valid oracle and source evidence')
    return {**thresholds, 'calibration': {'completed_before_measurements': True,
            'method': 'fixed pilot bounds; smoke qualifies evidence, never fits measurement error',
            'cells': calibration}}


def resume_fingerprint(root: Path | None = None) -> dict:
    root = root or r.ROOT
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((root / 'evaluation').rglob('*'))
            if p.is_file() and p.suffix in ('.py', '.json', '.js', '.go', '.yaml', '.yml') and '__pycache__' not in p.parts}


def lock_runtime_images(run_dir: Path, images: list[dict]) -> None:
    path=run_dir/'runtime-image-lock.json'
    locked=json.loads(path.read_text()) if path.exists() else {}
    for image in images:
        name,digest=image.get('image'),image.get('image_id')
        if not name or not digest:
            raise RuntimeError('running image has no resolved immutable ID')
        if name in locked and locked[name]!=digest:
            raise RuntimeError(f'runtime image changed during study: {name}')
        locked[name]=digest
    r.write(path,locked)


def extract_events(log: str, kind: str) -> list[dict]:
    records = []
    for line in log.splitlines():
        marker = 'EVAL_' + kind + ' '
        if marker not in line:
            continue
        value = line.split(marker, 1)[1]
        # k6's default console logger quotes/escapes its payload.
        if value.endswith('" source=console'):
            value = json.loads('"' + value.rsplit('" source=console', 1)[0] + '"')
        records.append(json.JSONDecoder().raw_decode(value)[0])
    return records


def extract_ledger(log: str) -> list[dict]:
    return extract_events(log, 'REQUEST')


def sleep_bounded(seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if r._DEADLINE is not None and time.monotonic() >= r._DEADLINE:
            raise TimeoutError('eight-hour study deadline reached')
        time.sleep(min(5, end - time.monotonic()))


def observer_targets(cell, *, include_agent=False):
    targets = [('frontend', 'frontend' if cell.demo == 'boutique' else 'frontend-proxy', False),
               ('checkout', 'checkoutservice' if cell.demo == 'boutique' else 'checkout', True)]
    if include_agent and cell.demo=='astronomy':targets.append(('agent','agent',False))
    return targets


def install_observers(cell, path: Path, *, include_agent=False, setup_no_surge=False) -> None:
    for boundary, name, grpc in observer_targets(cell,include_agent=include_agent):
        service = r.kube_json('get', 'service', name, '-n', cell.namespace)
        deployment = r.kube_json('get', 'deployment', name, '-n', cell.namespace)
        target = service['spec']['ports'][0].get('targetPort', service['spec']['ports'][0]['port'])
        if isinstance(target, str):
            target = next(p['containerPort'] for c in deployment['spec']['template']['spec']['containers']
                          for p in c.get('ports', []) if p.get('name') == target)
        r.write(path / f'observer-before-{name}.json', {'service': service, 'deployment': deployment})
        config = observer_config(boundary, target, grpc=grpc)
        r.apply({'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {
            'name': f'eval-observer-{boundary}', 'namespace': cell.namespace,
            'labels': {r.OWNER_LABEL: r.OWNER_VALUE}}, 'data': {'nginx.conf': config}})
        patch = observer_patch(boundary, r.CONFIG['observer_image'])
        strategy = deployment['spec'].get('strategy') if setup_no_surge else None
        replace_during_setup = strategy is not None and strategy.get('type') == 'RollingUpdate'
        if replace_during_setup:
            # Setup has no workload traffic. Free the old pod's pinned-node
            # capacity before scheduling the pod with the observer.
            patch['spec']['strategy'] = {'type': 'RollingUpdate',
                                         'rollingUpdate': {'maxSurge': 0, 'maxUnavailable': 1}}
        r.kube('patch', 'deployment', name, '-n', cell.namespace, '--type=strategic',
               '-p', json.dumps(patch))
        r.kube('rollout', 'status', f'deployment/{name}', '-n', cell.namespace, '--timeout=240s')
        if replace_during_setup:
            r.kube('patch', 'deployment', name, '-n', cell.namespace, '--type=json',
                   '-p', json.dumps([{'op': 'replace', 'path': '/spec/strategy', 'value': strategy}]))
        r.kube('patch', 'service', name, '-n', cell.namespace, '--type=merge',
               '-p', json.dumps(service_patch(service, grpc=grpc)))
        r.write(path / f'observer-after-{name}.json', {
            'service': r.kube_json('get', 'service', name, '-n', cell.namespace),
            'deployment': r.kube_json('get', 'deployment', name, '-n', cell.namespace)})


def install_scripts(cell) -> None:
    data = {p.name: p.read_text() for p in (r.ROOT / 'evaluation/workloads').iterdir()
            if p.suffix in ('.js', '.py', '.json')}
    for name in ('eval-k6-script', 'eval-matched-locust-script'):
        r.apply({'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {
            'name': name, 'namespace': cell.namespace, 'labels': {r.OWNER_LABEL: r.OWNER_VALUE}},
            'data': data})


def export_window(cell, path: Path, start: str, end: str, seconds: int, *, evidence_path=None) -> None:
    import urllib.parse
    for ns, prefix in ((cell.namespace, 'k8s'), (r.KARTHOS_NAMESPACE, 'karthos')):
        for kind in ('pods', 'services', 'events'):
            try: r.write(path / f'{prefix}-{kind}.json', r.kube_json('get', kind, '-n', ns))
            except Exception as exc: r.write(path / f'{prefix}-{kind}.error', str(exc))
    try: r.write(path / 'k8s-nodes.json', r.kube_json('get', 'nodes'))
    except Exception as exc: r.write(path/'k8s-nodes.error',str(exc))
    # Independent stages remain exportable when a different stage is down.
    services_doc = path/'karthos-services.json'
    services = json.loads(services_doc.read_text()).get('items',[]) if services_doc.exists() else []
    vm = next((s['metadata']['name'] for s in services if 'victoria' in s['metadata']['name']), None)
    if vm:
        try:
            with r.forward(f'service/{vm}', r.KARTHOS_NAMESPACE, 18428, 8428) as base:
                names='|'.join(name.replace('.','_') for name in container_metrics(r.CONFIG.get('thesis_metric_contract',False)))
                selector = '{__name__=~"servicegraph_request_.*|'+names+'"}'
                from . import export_horizon
                horizon = export_horizon.resolve(evidence_path or path, start, end)
                r.write(path/'vm-export-horizon.json', horizon)
                query = urllib.parse.urlencode({'match[]': selector,
                    'start': export_horizon.iso(horizon['export_start_epoch']),
                    'end': export_horizon.iso(horizon['export_end_epoch'])})
                with r.urllib.request.urlopen(base + '/api/v1/export?' + query, timeout=r.http_timeout(15)) as response:
                    raw = response.read().decode()
                    r.write(path / 'vm-raw.ndjson', raw)
                    r.write(path/'vm-export-coverage.json', export_horizon.coverage(evidence_path or path, raw, horizon))
        except Exception as exc:
            r.write(path / 'vm-raw.error', str(exc))
    else:
        r.write(path/'vm-raw.error','VictoriaMetrics service unavailable in inventory')
    try:
        with r.forward('deployment/karthos-backend', r.KARTHOS_NAMESPACE, 18081, 8080) as base:
            for filename, route, params, key in metric_queries(start,end,seconds,source_id(cell.source),thesis=r.CONFIG.get('thesis_metric_contract',False)):
                try:
                    r.write(path / filename, r.api_pages(base, route, params, key,
                                                        pages_dir=path/'api-pages'/filename))
                except Exception as exc:
                    r.write(path / (filename + '.error'), str(exc))
    except Exception as exc:
        r.write(path/'api-export.error',str(exc))


def loggable_pod(pods: dict, container_name: str) -> str | None:
    """Logs exist only after the requested container starts (or terminates)."""
    for pod in pods.get('items', []):
        for status in pod.get('status', {}).get('containerStatuses', []):
            state = status.get('state', {})
            if status.get('name') == container_name and ('running' in state or 'terminated' in state):
                return pod['metadata']['name']
    return None


def generator_log_complete(status: dict) -> bool:
    return bool(status.get('verified') and status.get('semantic_complete'))


def cleanup_generator(name: str, namespace: str, path: Path, artifacts_verified: bool) -> None:
    if not artifacts_verified:
        r.write(path/'generator-retained.json', dict(job=name, namespace=namespace,
                reason='artifact export not verified; no ACK or automatic deletion',
                recovery_seconds=r.CONFIG.get('artifact_recovery_seconds', 1800)))
        return
    r.kube('delete','job',name,'-n',namespace,'--ignore-not-found',
           '--wait=true','--timeout=30s',check=False,timeout=35)


def reserve_window_budget(duration_seconds: int,config: dict,remaining: float | None) -> None:
    required=(duration_seconds+180+config['capture_preroll_seconds']+
              config['artifact_export_seconds']+
              config['telemetry_drain_seconds']+config.get('instrumentation_budget_seconds',0))
    if remaining is not None and remaining<required:
        raise TimeoutError('insufficient study budget for workload, export and telemetry drain')


def configure_generator_deadline(job: dict,duration: int,config: dict,
                                 remaining: float | None,now_epoch: float) -> None:
    # Setup/pre-roll have already consumed time; gate again before apply.
    reserve_window_budget(duration,{**config,'capture_preroll_seconds':0},remaining)
    ordinary=duration+180+config['artifact_export_seconds']
    if remaining is not None:
        ordinary=min(ordinary,remaining)
        env=job['spec']['template']['spec']['containers'][0]['env']
        env[:]=[x for x in env if x['name']!='EVAL_STUDY_DEADLINE_EPOCH']
        env.append(dict(name='EVAL_STUDY_DEADLINE_EPOCH',value=str(int(now_epoch+remaining))))
    job['spec']['activeDeadlineSeconds']=max(1,int(ordinary+config['artifact_recovery_seconds']))


def capture_preroll_seconds(config: dict) -> int:
    seconds = int(config['capture_preroll_seconds'])
    if seconds < config['thresholds']['max_freshness_seconds']:
        raise ValueError('capture pre-roll must cover the permitted cached history')
    return seconds


def native_window_bounds(pods: dict, container_name: str) -> tuple | None:
    for pod in pods.get('items', []):
        for status in pod.get('status', {}).get('containerStatuses', []):
            if status.get('name') == container_name:
                terminated = status.get('state', {}).get('terminated', {})
                if terminated.get('startedAt') and terminated.get('finishedAt'):
                    return terminated['startedAt'], terminated['finishedAt']
    return None


def run_window(cell, parent: Path, spec: dict, run_id: str, *, native=False) -> dict:
    from .manifests import k6_job, matched_locust_job, locust_job
    from .scoring import evaluate_window
    from .generator_execution import execute_generator
    from .verify import parse_time
    reserve_window_budget(spec['duration_seconds'],r.CONFIG,
                            None if r._DEADLINE is None else r._DEADLINE-time.monotonic())
    path=parent/'windows'/spec['name']
    path.mkdir(parents=True,exist_ok=False)
    target='http://frontend:80' if cell.demo=='boutique' else 'http://frontend-proxy:8080'
    mode='native' if native else 'matched'
    recovery=r.CONFIG['artifact_recovery_seconds']
    kwargs=dict(duration_seconds=spec['duration_seconds'],artifact_mode=True,recovery_seconds=recovery)
    if native:
        if spec.get('protocol_version',2)>=4:kwargs['native_ledger']=True
        job=locust_job(cell.demo,spec['name'],cell.namespace,r.CONFIG[f'{cell.demo}_locust_image'],target,**kwargs)
    else:
        kwargs.update(rate=spec['rate'],workload=spec['workload'])
        builder=k6_job if spec['generator']=='k6' else matched_locust_job
        image=r.CONFIG['k6_image'] if spec['generator']=='k6' else r.CONFIG[f'{cell.demo}_locust_image']
        job=builder(cell.demo,cell.source,spec['name'],cell.namespace,image,target,**kwargs)
    window_id=run_id+':'+cell.slug+':'+spec['name']
    environment=job['spec']['template']['spec']['containers'][0]['env']
    environment[:]=[x for x in environment if x['name']!='RUN_ID']
    environment.append(dict(name='RUN_ID',value=window_id))
    if spec.get('protocol_version',2)>=4:environment.append(dict(name='EVAL_CORRELATE_TRACES',value='1'))
    # Recovery retention is distinct from the workload's nominal duration.
    job['spec']['activeDeadlineSeconds']=spec['duration_seconds']+180+r.CONFIG['artifact_export_seconds']+recovery
    r.write(path/'job.json',job)
    manifest={**spec,'cell':cell.slug,'demo':cell.demo,'source':cell.source,
              'source_id':source_id(cell.source),'namespace':cell.namespace,'repeat':cell.repeat,
              'protocol_version':spec.get('protocol_version',2),'artifact_protocol':1,'run_id':window_id,'study_run_id':run_id,
              'mode':mode,'phase':'starting','controller_start':r.utc(),
              'generator_evidence_mode':'native-ledger' if native and spec.get('protocol_version',2)>=4 else mode}
    r.write(path/'manifest.json',manifest)
    capture=None
    artifacts_verified=False
    try:
        if manifest['protocol_version']>=4:
            from .structured_capture import RepairCapture
            if r._DEADLINE is None:raise RuntimeError('protocol4 requires an absolute study budget')
            deadline_epoch=time.time()+max(0,r._DEADLINE-time.monotonic())
            capture_settings=dict(r.CONFIG,capture_expected_seconds=spec['duration_seconds']+180+
                r.CONFIG['capture_preroll_seconds']+r.CONFIG['telemetry_drain_seconds']+
                r.CONFIG['artifact_export_seconds']+2*90+r.CONFIG.get('artifact_recovery_seconds',1800))
            capture=RepairCapture(cell,path,deadline_epoch,config=capture_settings).start()
            manifest['phase']='counter_priming'
            r.write(path/'manifest.json',manifest)
            from .repair_pilot import prime_after_instrumentation
            manifest['counter_priming']=prime_after_instrumentation(cell,path,run_id+':'+spec['name'],parent.parent.parent)
        else:
            capture=ContinuousCapture(cell,path).start()
        manifest.update(capture_started_at=capture.started_at,
                        capture_preroll_seconds=capture_preroll_seconds(r.CONFIG),phase='capture_preroll')
        r.write(path/'manifest.json',manifest)
        sleep_bounded(manifest['capture_preroll_seconds'])
        if manifest['protocol_version'] >= 3:
            from .semantics_pilot import quiet_counter
            quiet_counter(cell,path,'before')
        configure_generator_deadline(job,spec['duration_seconds'],r.CONFIG,
                                     None if r._DEADLINE is None else r._DEADLINE-time.monotonic(),time.time())
        r.write(path/'job.json',job)
        manifest['phase']='generator_and_artifact_export'
        r.write(path/'manifest.json',manifest)
        result=execute_generator(job,cell.namespace,path,window_id,spec['generator'],manifest['generator_evidence_mode'],
                                 export_timeout_seconds=r.CONFIG['artifact_export_seconds'])
        artifacts_verified=generator_log_complete(result)
        manifest.update(generator_log_complete=artifacts_verified,
                        generator_artifacts_complete=artifacts_verified,
                        generator_job_failed=result['workload_exit_code']!=0,
                        workload_exit_code=result['workload_exit_code'],
                        kubernetes_job_failed=result.get('kubernetes_job_failed',False),
                        generator_log_pod=result.get('pod_name'))
        lock_runtime_images(parent.parent.parent,r.image_ids(json.loads((path/'generator-pods.json').read_text())))
        records=json.loads((path/'generator-window.json').read_text())
        ledger=[json.loads(x) for x in (path/'ledger.ndjson').read_text().splitlines() if x.strip()]
        if manifest['protocol_version']>=4:
            from .repair_pilot import window_bounds
            manifest.update(window_bounds(records[0]['started_at'],records[-1]['ended_at'],spec['duration_seconds'],ledger))
        elif native:
            manifest.update(start=result['started_at'],end=result['ended_at'],
                            window_semantics='native workload process lifetime including report generation; excludes artifact hold')
        else:
            manifest['start']=records[0]['started_at']
            scheduled=parse_time(manifest['start'])+timedelta(seconds=spec['duration_seconds'])
            candidates=[records[-1]['ended_at'],scheduled.isoformat()]+[x['completed_at'] for x in ledger]
            manifest['end']=max(candidates,key=parse_time)
        manifest['observation_seconds']=(parse_time(manifest['end'])-parse_time(manifest['start'])).total_seconds()
        manifest['phase']='drain'
        r.write(path/'manifest.json',manifest)
        sleep_bounded(r.CONFIG['telemetry_drain_seconds'])
        if manifest['protocol_version'] >= 3:
            quiet_counter(cell,path,'after')
    except BaseException as exc:
        manifest.update(phase='incomplete',interrupted=True,error=str(exc),finished_at=r.utc(),
                        generator_artifacts_complete=artifacts_verified)
        # Already exported files remain diagnostic even when semantic completeness failed.
        raw=path/'generator-artifacts'/'generator.log'
        if raw.exists() and not (path/'generator.log').exists():
            r.write(path/'generator-interrupted.log',raw.read_text(errors='replace'))
        manifest.setdefault('start',manifest['controller_start'])
        manifest.setdefault('end',r.utc())
        r.write(path/'manifest.json',manifest)
        saved=r._DEADLINE
        r._DEADLINE=time.monotonic()+45
        try:
            export_window(cell,path,manifest['start'],manifest['end'],max(60,spec['duration_seconds']))
        except Exception as export_error:
            r.write(path/'interrupted-export.error',str(export_error))
        finally:
            r._DEADLINE=saved
        raise
    finally:
        if capture:
            try:capture.stop()
            except Exception as exc:r.write(path/'capture-stop.error',str(exc))
        saved=r._DEADLINE
        r._DEADLINE=time.monotonic()+40
        try:
            cleanup_generator(job['metadata']['name'],cell.namespace,path,artifacts_verified)
        except Exception as exc:r.write(path/'generator-cleanup.error',str(exc))
        finally:
            r._DEADLINE=saved
            r.checksums(path)
    seconds=max(60,round(manifest['observation_seconds']))
    manifest.update(metric_query_window_seconds=seconds,
                    metric_query_start=(parse_time(manifest['end'])-timedelta(seconds=seconds)).isoformat(),
                    metric_query_end=manifest['end'],
                    metric_query_start_offset_seconds=manifest['observation_seconds']-seconds)
    r.write(path/'manifest.json',manifest)
    export_window(cell,path,manifest['start'],manifest['end'],seconds)
    if manifest['protocol_version'] >= 3:
        from .semantics_pilot import export_parity
        export_parity(cell,path,manifest['start'],manifest['end'],seconds)
    manifest.update(phase='complete',finished_at=r.utc())
    r.write(path/'manifest.json',manifest)
    result=evaluate_window(path,r.CONFIG['thresholds'])
    r.write(path/'verification.json',result)
    r.checksums(path)
    if not native and not result.get('oracle_valid',False):
        raise RuntimeError(f'invalid generator/source oracle: {cell.slug}/{spec["name"]}; see verification.json')
    return result


def run_cell(cell, run_dir: Path, *, smoke: bool, replace_existing: bool,
             specs: list[dict] | None = None, include_native: bool = True, platform_tag: str | None = None, phase_name: str | None = None) -> dict:
    from .manifests import k6_job
    path = run_dir / (phase_name or ('smoke' if smoke else 'measured')) / cell.slug
    path.mkdir(parents=True, exist_ok=False)
    status = {'cell': cell.slug, 'smoke': smoke, 'phase': 'reset', 'started_at': r.utc(), 'windows': []}
    r.write(path / 'status.json', status)
    try:
        r.reset_cell(replace_existing)
        r.prepare_source(cell.source)
        r.create_namespace(cell)
        status['phase'] = 'demo'
        r.write(path / 'status.json', status)
        r.install_demo(cell, path)
        install_observers(cell, path,include_agent=any(spec.get('workload')=='native' for spec in (specs or [])))
        status['phase'] = 'karthos'
        r.write(path / 'status.json', status)
        r.deploy_karthos(cell, path)
        ids=json.loads((path/'locked-image-ids.json').read_text())
        lock_runtime_images(run_dir,ids['demo']+ids['karthos'])
        if platform_tag:
            from .semantics_pilot import validate_platform_images
            validate_platform_images(ids['karthos'],platform_tag)
        install_scripts(cell)
        target = 'http://frontend:80' if cell.demo == 'boutique' else 'http://frontend-proxy:8080'
        if specs is not None and any(x.get('protocol_version',0)>=3 for x in specs):
            from .semantics_pilot import run_warmup
            status['warmup']=run_warmup(cell,path,run_dir.name)
            r.write(path/'status.json',status)
        else:
            warm = k6_job(cell.demo, cell.source, 'warmup', cell.namespace, r.CONFIG['k6_image'], target,
                          duration_seconds=20, rate=1, workload='checkout-heavy')
            r.job_and_ledger(warm, cell.namespace, path, 'warmup')
            r.kube('delete', 'job', warm['metadata']['name'], '-n', cell.namespace, '--wait=true')
        sleep_bounded(r.CONFIG['telemetry_drain_seconds'])
        for spec in (specs if specs is not None else windows(cell.repeat, smoke)):
            status['phase'] = spec['name']
            r.write(path / 'status.json', status)
            result = run_window(cell, path, spec, run_dir.name, native=spec.get('workload')=='native')
            status['windows'].append({'name': spec['name'], 'state': result['state'],
                                       'oracle_valid': result['oracle_valid'],
                                       'source_valid': result['source_valid'],
                                       'unexpected_outcomes': result['generator']['unexpected_outcomes']})
        if include_native and not smoke and cell.repeat == 1:
            native = {'name': 'locust-native', 'generator': 'locust', 'workload': 'native',
                      'duration_seconds': r.CONFIG['locust_duration_seconds'], 'rate': 0}
            result = run_window(cell, path, native, run_dir.name, native=True)
            status['windows'].append({'name': native['name'], 'state': result['state']})
        status.update(phase='complete', finished_at=r.utc())
    except BaseException as exc:
        status.update(phase='failed', error=str(exc), finished_at=r.utc())
        saved=r._DEADLINE
        r._DEADLINE=time.monotonic()+45
        try:
            setup_diagnostics(cell,path)
        except Exception as diagnostic_error:
            r.write(path/'setup-diagnostics.error',str(diagnostic_error))
        finally:
            r._DEADLINE=saved
        raise
    finally:
        r.write(path / 'status.json', status)
        r.checksums(path)
    return status


def setup_diagnostics(cell, path: Path):
    """Bounded deployment evidence even when the first scoring window never ran."""
    for ns in (cell.namespace,r.KARTHOS_NAMESPACE):
        for kind in ('pods','events','services'):
            try:
                data=r.kube_json('get',kind,'-n',ns)
                r.write(path/'setup-diagnostics'/f'{ns}-{kind}.json',data)
                if kind=='pods':
                    from .capture import stream_targets
                    for namespace,pod,container in stream_targets(data):
                        output=r.kube('logs','-n',namespace,pod,'-c',container,'--tail=150',
                                      check=False,timeout=5)
                        r.write(path/'setup-diagnostics'/f'{pod}__{container}.log',output.stdout+output.stderr)
            except Exception as exc:
                r.write(path/'setup-diagnostics'/f'{ns}-{kind}.error',str(exc))


def verify_study(run_dir: Path) -> dict:
    from .scoring import evaluate_window, aggregate_study
    frozen = run_dir / 'frozen-thresholds.json'
    if not frozen.exists():
        raise RuntimeError('measured verification requires frozen thresholds')
    thresholds = json.loads(frozen.read_text())
    for path in sorted((run_dir / 'measured').glob('*/windows/*')):
        if (path / 'manifest.json').exists():
            try:
                result = evaluate_window(path, thresholds)
                r.write(path / 'verification.json', result)
            except Exception as exc:
                r.write(path / 'verification.json', {'state': 'incomplete', 'error': str(exc)})
            r.checksums(path)
    result = aggregate_study(run_dir)
    r.write(run_dir / 'aggregate-verification.json', result)
    render_report(run_dir, result)
    r.checksums(run_dir)
    return result


def render_report(run_dir: Path, aggregate: dict):
    lines=['# Kivel pilot v2 evidence report','',
           'Execution completion and metric agreement are reported separately. Native Locust runs are descriptive.', '',
           f"Aggregate measurement state: **{aggregate.get('state','incomplete')}**",'',
           f"Matched windows attempted: {aggregate.get('attempted_matched_windows',0)}/48.",
           f"Native windows retained: {len(aggregate.get('native_descriptive_windows',[]))}/4.",'',
           '| Cell | Generator / workload | Score | Source usable | Attempts / terminal records | Unexpected outcomes |',
           '|---|---|---|---|---|---|']
    for item in aggregate.get('windows',[]):
        m,g=item['manifest'],item['generator']
        lines.append(f"| {m.get('cell')} | {m.get('generator')} / {m.get('workload')} | {item['state']} | "
                     f"{item.get('source_valid')} | {g.get('attempts',0)} / {g.get('requests',0)} | {g.get('unexpected_outcomes',0)} |")
    lines += ['','## Stage differences','',
              'A missing stage remains unknown. These tables identify where evidence stops agreeing; they do not infer a cause without supporting records.','']
    for item in aggregate.get('windows',[]):
        m=item['manifest']
        lines += [f"### {m.get('cell')} / {m.get('generator')} / {m.get('workload')}",'',
                  '| Stage | State | Evidence |','|---|---|---|']
        for row in item.get('stage_table',[]):
            detail=json.dumps({k:v for k,v in row.items() if k not in ('state','stage')},sort_keys=True)
            lines.append(f"| {row.get('stage')} | {row.get('state')} | {detail.replace('|','/')} |")
        lines += ['', 'Reasons: '+('; '.join(item.get('reasons',[])) or 'see numeric stage results'), '']
    lines += ['## Decisions for GKE','',
              'Compare matched workload delivery, ledger completeness, per-stage error, repeat variation, and generator footprint in `aggregate-verification.json` → `generator_comparison`.',
              'Keep native Locust results separate: a different journey mix cannot establish that one generator measures more accurately.',
              'The pilot does not automatically select a provider, application, generator, or thesis threshold. Retain limits and fixed reruns with their own IDs.','']
    r.write(run_dir/'report.md','\n'.join(lines))


def _execute_matrix(run_dir: Path, report: dict, *, replace_existing=False, smoke_only=False):
    for cell in cells_for_repeat(1):
        status_file = run_dir / 'smoke' / cell.slug / 'status.json'
        if status_file.exists() and json.loads(status_file.read_text()).get('phase') == 'complete':
            continue
        _archive_attempt(run_dir, cell, smoke=True)
        assert_pins(report['pins'])
        report['cells'].append(run_cell(cell, run_dir, smoke=True, replace_existing=replace_existing))
        r.write(run_dir / 'run-report.json', report)
    if not (run_dir / 'frozen-thresholds.json').exists():
        calibration=[]
        for cell in cells_for_repeat(1):
            smoke_windows=json.loads((run_dir/'smoke'/cell.slug/'status.json').read_text())['windows']
            calibration.append({'cell':cell.slug,'valid':smoke_evidence_valid(smoke_windows),
                                'windows':smoke_windows})
        r.write(run_dir / 'frozen-thresholds.json', freeze_thresholds(r.CONFIG['thresholds'], calibration))
    if smoke_only:
        report['state'] = 'smoke_complete'
        return
    for repeat in range(1, 4):
        for cell in cells_for_repeat(repeat):
            status_file = run_dir / 'measured' / cell.slug / 'status.json'
            if status_file.exists() and json.loads(status_file.read_text()).get('phase') == 'complete':
                continue
            _archive_attempt(run_dir, cell, smoke=False)
            assert_pins(report['pins'])
            report['cells'].append(run_cell(cell, run_dir, smoke=False, replace_existing=replace_existing))
            r.write(run_dir / 'run-report.json', report)
    report['aggregate'] = verify_study(run_dir)
    report['state'] = 'complete'


def _archive_attempt(run_dir, cell, *, smoke):
    path = run_dir / ('smoke' if smoke else 'measured') / cell.slug
    if not path.exists():
        return
    saved = run_dir / 'failed-attempts'
    saved.mkdir(exist_ok=True)
    index = 1
    while (saved / f'{"smoke" if smoke else "measured"}-{cell.slug}-{index}').exists():
        index += 1
    path.rename(saved / f'{"smoke" if smoke else "measured"}-{cell.slug}-{index}')


def assert_pins(pins: dict) -> None:
    if r.repository_fingerprint(r.REPO) != pins['karthos_source_hash']:
        raise RuntimeError('Karthos source content changed during the study')
    if r.tree_digest(r.CACHE/'karthos-chart') != pins['karthos_chart_hash']:
        raise RuntimeError('staged Karthos chart changed during the study')
    if r.tree_digest(r.CACHE/'opentelemetry-demo') != pins['astronomy_chart_hash']:
        raise RuntimeError('staged Astronomy chart changed during the study')
    if r.tree_digest(r.CACHE/'boutique',exclude={'.git'}) != pins['boutique_source_hash']:
        raise RuntimeError('staged Boutique source changed during the study')


def select_platform_tag(pins):
    import os
    tag='eval-'+pins['karthos_revision'][:12]+'-'+pins['karthos_source_hash'][:8]
    os.environ['IMAGE_TAG']=tag
    os.environ['KARTHOS_IMAGE_TAG_OVERRIDE']=tag
    return tag


def run_study(args):
    r.OUT.mkdir(exist_ok=True)
    run_dir = r.OUT / (args.run_id or datetime.now(timezone.utc).strftime('pilot-v2-%Y%m%dT%H%M%SZ'))
    run_dir.mkdir(exist_ok=False)
    r._DEADLINE = time.monotonic() + r.CONFIG['study_deadline_seconds']
    report = {'protocol_version': 2, 'run_dir': str(run_dir), 'started_at': r.utc(),
              'state': 'running', 'cells': [], 'expected_matched_windows': 48, 'expected_native_windows': 4}
    r.write(run_dir / 'run-report.json', report)
    try:
        report['preflight'] = r.preflight(args.replace_existing)
        report['pins'] = r.stage()
        report['platform_image_tag'] = select_platform_tag(report['pins'])
        r.write(run_dir / 'locked-config.json', r.CONFIG)
        r.write(run_dir / 'locked-harness.json', resume_fingerprint())
        r.shutil.copytree(r.ROOT / 'evaluation', run_dir / 'locked-harness',
                          ignore=r.shutil.ignore_patterns('__pycache__'))
        r.write(run_dir/'run-report.json',report)
        # The user requested Karthos source isolation; image builds only read it.
        r.cmd([str(r.REPO / 'test/e2e/prepare-platform-images.sh')], cwd=r.REPO,
              capture=run_dir / 'image-build.log', timeout=1800)
        report['images_prepared_at']=r.utc()
        r.write(run_dir/'run-report.json',report)
        _execute_matrix(run_dir, report, replace_existing=args.replace_existing, smoke_only=args.smoke_only)
    except BaseException as exc:
        report.update(state='incomplete', error=str(exc))
        raise
    finally:
        if report['state']=='incomplete' and (run_dir/'frozen-thresholds.json').exists():
            try: report['aggregate']=verify_study(run_dir)
            except Exception as exc: report['verification_error']=str(exc)
        report.update(finished_at=r.utc(), attempts=r.attempt_statuses(run_dir))
        r.write(run_dir / 'run-report.json', report)
        r.checksums(run_dir)
        r._DEADLINE = None
        print('evidence:', run_dir, flush=True)


def resume_study(run_dir: Path):
    run_dir = run_dir.resolve()
    report = json.loads((run_dir / 'run-report.json').read_text())
    if report['state'] not in ('incomplete', 'smoke_complete'):
        raise RuntimeError('only incomplete or smoke-complete protocol v2 studies can resume')
    if json.loads((run_dir / 'locked-config.json').read_text()) != r.CONFIG:
        raise RuntimeError('configuration changed; start a new study')
    if json.loads((run_dir / 'locked-harness.json').read_text()) != resume_fingerprint():
        raise RuntimeError('harness or workload changed; start a new study')
    elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(report['started_at'].replace('Z', '+00:00'))).total_seconds()
    remaining = r.CONFIG['study_deadline_seconds'] - elapsed
    if remaining <= 0:
        raise TimeoutError('original eight-hour study deadline reached')
    r._DEADLINE = time.monotonic() + remaining
    try:
        r.preflight(False)
        if r.stage() != report['pins']:
            raise RuntimeError('Karthos or demo source pins changed; start a new study')
        if select_platform_tag(report['pins']) != report.get('platform_image_tag'):
            raise RuntimeError('platform image tag changed')
        report['state'] = 'running'
        _execute_matrix(run_dir, report)
    except BaseException as exc:
        report.update(state='incomplete', error=str(exc))
        raise
    finally:
        if report['state']=='incomplete' and (run_dir/'frozen-thresholds.json').exists():
            try: report['aggregate']=verify_study(run_dir)
            except Exception as exc: report['verification_error']=str(exc)
        report.update(finished_at=r.utc(), attempts=r.attempt_statuses(run_dir))
        r.write(run_dir / 'run-report.json', report)
        r.checksums(run_dir)
        r._DEADLINE = None
