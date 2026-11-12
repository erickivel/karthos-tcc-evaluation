"""Live adapters for the pre-measurement mechanism check."""
from __future__ import annotations

import gzip
import io
import json
import math
import re
import statistics
import threading
import time

from . import export
from .models import read
from .readiness import RESERVE, UnsafeReadiness, cutoff
from ..protocol import NAMESPACES, source_id
from ..scoring import stamp


REQUEST_COUNT = 'servicegraph.request.count'
CHECKOUT = {'boutique': 'checkoutservice', 'astronomy': 'checkout'}
RAW_REQUEST_COUNT = {'mesh': 'istio_requests_total', 'trace': 'traces_service_graph_request_total'}


def _sample_rejections(sample, spec):
    labels, edge = sample.get('labels', {}), sample.get('edge') or {}
    caller, target = labels.get('edge_client_id'), labels.get('edge_server_id')
    namespace = NAMESPACES[spec['demo']]
    value = sample.get('value')
    checks = {
        'metric': sample.get('metric_name') == REQUEST_COUNT,
        'unit': sample.get('unit') == 'count',
        'source': labels.get('source') == source_id(spec['source']),
        'caller': labels.get('edge_client') == 'frontend',
        'target': labels.get('edge_server') == CHECKOUT[spec['demo']],
        'entity-edge-identity': bool(caller and target and sample.get('entity_id') == caller
                                     and edge.get('source_id') == caller and edge.get('target_id') == target),
        'namespace': labels.get('client_namespace', namespace) == namespace and labels.get('server_namespace', namespace) == namespace,
        'virtual-node': not labels.get('virtual_node'),
        'temporality': labels.get('temporality') in (None, 'cumulative'),
        'value': type(value) in (int, float) and math.isfinite(value) and value >= 0,
    }
    return [reason for reason, valid in checks.items() if not valid]


def _checkout_samples(payload, spec):
    query = payload.get('data', {}).get('query') or {}
    if query.get('aggregation') != 'latest' or query.get('timestamp_semantics') != 'evaluation':
        raise ValueError('public API did not confirm latest counter evaluation')
    for sample in payload['data'].get('samples', []):
        if not _sample_rejections(sample, spec):
            yield sample


def _counter_identity(sample):
    return sample.get('entity_id'), tuple(sorted(sample['labels'].items()))


def _raw_rejections(sample, raw):
    aliases = {
        'http_status_code': ('http_response_status_code', 'response_code'),
        'client_namespace': ('client_k8s.namespace.name', 'client_k8s_namespace_name'),
        'server_namespace': ('server_k8s.namespace.name', 'server_k8s_namespace_name'),
        'client_http_method': ('client_http.method',),
        'server_http_method': ('server_http.method',),
    }
    synthetic = {'edge_client', 'edge_server', 'edge_client_id', 'edge_server_id', 'source', 'temporality'}
    labels = raw['labels']
    reasons = []
    for field, actual, expected in (
            ('value', raw['value'], sample['value']),
            ('source', labels.get('karthos_source'), sample['labels']['source']),
            ('caller', labels.get('client'), 'frontend'),
            ('target', labels.get('server'), sample['labels']['edge_server'])):
        if actual != expected:
            reasons.append({'field':field, 'actual':actual, 'expected':expected})
    if labels.get('virtual_node'):
        reasons.append({'field':'virtual_node', 'actual':labels['virtual_node'], 'expected':None})
    if labels.get('karthos_source') == 'mesh.istio' and labels.get('reporter') != 'destination':
        reasons.append({'field':'reporter', 'actual':labels.get('reporter'), 'expected':'destination'})
    for key, value in sample['labels'].items():
        if key in synthetic:
            continue
        candidates = (key, key.replace('.', '_'), *aliases.get(key, ()))
        if not any((str(labels[name]).lower() if type(labels[name]) is bool else str(labels[name])) == value
                   for name in candidates if name in labels):
            reasons.append({'field':key, 'expected':value, 'actual':{name:labels[name] for name in candidates if name in labels}})
    return reasons


def _raw_matches(sample, raw):
    return not _raw_rejections(sample, raw)


def _counter_evidence(before, after, raw_points, start, observed_at, rejections=None):
    rejections = rejections if rejections is not None else []
    def reject(sample, reason, **details):
        rejections.append(dict(sample=sample, reasons=[reason], **details))
    previous = {_counter_identity(sample): sample for sample in before}
    evidence = {}
    for sample in sorted(after, key=lambda row: stamp(row['timestamp'])):
        at = stamp(sample['timestamp'])
        if at > observed_at:
            reject(sample, "future-evaluation")
            continue
        key = _counter_identity(sample)
        prior = previous.get(key)
        if prior is not None and at < stamp(prior['timestamp']):
            reject(sample, 'older-than-baseline', previous=prior)
            continue
        previous[key] = sample
        if at <= start or sample['value'] <= 0 or key in evidence:
            reject(sample, 'baseline-evaluation' if at <= start else 'nonpositive-or-already-evidenced')
            continue
        prior_at = stamp(prior['timestamp']) if prior is not None else None
        if prior is not None and (at == prior_at or sample['value'] == prior['value']):
            reject(sample, 'unchanged-value' if sample['value'] == prior['value'] else 'unchanged-evaluation', previous=prior)
            continue
        if prior is not None and sample['value'] > prior['value']:
            evidence[key] = {'method': 'same-series-increase', 'previous': prior, 'sample': sample}
            continue
        # A first observation needs a fresh producer point. A decrease also
        # needs a new producer epoch to qualify before the counter grows again.
        for raw in raw_points:
            raw_at = stamp(raw['timestamp'])
            if not start <= raw_at <= at or not _raw_matches(sample, raw):
                continue
            if prior is not None:
                epoch = stamp(raw['started_at']) if raw.get('started_at') is not None else None
                if epoch is None or not start <= epoch <= raw_at or epoch <= prior_at:
                    continue
            evidence[key] = {'method': 'reset-corroborated' if prior is not None else 'first-observation-corroborated',
                             'previous': prior, 'sample': sample, 'raw': raw}
            break
        if key not in evidence:
            reject(sample, 'reset-without-fresh-epoch' if prior is not None else 'no-fresh-full-identity-raw-match',
                   raw_candidates=[{'raw': raw, 'fresh': start <= stamp(raw['timestamp']) <= at,
                                    'full_identity_value_match': _raw_matches(sample, raw),
                                    'mismatches': _raw_rejections(sample, raw),
                                    'epoch':raw.get('started_at')} for raw in raw_points])
    return list(evidence.values())


def _cpu_memory_samples(capture, helpers):
    import gzip
    import tempfile
    expected = {row['uid']:row for row in helpers}
    observed = {}
    for writer in list(capture.sample_writers.values()):
        if not writer.path.name.startswith('kubelet-') or not writer.records:
            continue
        # Copy through a completed gzip member, then scan without holding the
        # writer or keeping all expanded Kubernetes responses in memory.
        with tempfile.TemporaryFile() as snapshot, writer.path.open('rb') as stream:
            remaining = writer.stored_bytes
            while remaining:
                block = stream.read(min(remaining,1024*1024))
                if not block:
                    raise UnsafeReadiness('kubelet sampler file truncated during snapshot')
                snapshot.write(block)
                remaining -= len(block)
            snapshot.seek(0)
            with gzip.open(snapshot,'rb') as records:
                for line in records:
                    row = json.loads(line)
                    for pod in row.get('summary', {}).get('pods', []):
                        uid = pod.get('podRef', {}).get('uid')
                        helper = expected.get(uid)
                        if helper is None or row.get('node_name')!=helper['node_name']:
                            continue
                        for container in pod.get('containers', []):
                            cpu, memory = container.get('cpu', {}), container.get('memory', {})
                            if (container.get('name')=='stimulus' and cpu.get('time') and memory.get('time')
                                    and cpu.get('usageCoreNanoSeconds', 0)>0 and memory.get('workingSetBytes', 0)>0):
                                observed[uid] = {'node':row['node_name'],'pod_uid':uid,'container':'stimulus',
                                                 'received_at':row['received_at'],'cpu':cpu,'memory':memory}
        if len(observed)==len(expected):
            break
    return observed


class ProbeDriver:
    def __init__(self, driver):
        self.driver = driver
        self.ctx = driver.ctx
        self.root = self.ctx.result/'readiness'
        self.capture = None
        self.group = None
        self.cell = None
        self.catalogued = set()
        self.broken_cells = set()
        self.unrecovered_restart_cells = set()
        self.setup_seconds = 0
        self.initial = None
        self.initial_disks = None
        self.preflight_seconds = 0
        self.restart_pods = set()

    def start(self):
        started = time.monotonic()
        self.driver.start_repeat(1)
        self.initial = self.inventory()
        self.initial_disks = self.provider_disks()
        # Includes initial provisioning as a conservative proxy for a rebuild.
        self.preflight_seconds = max(time.monotonic()-started, time.time()-self.ctx.state['started_at'])

    def _close(self):
        if self.capture is None:
            return
        capture, self.capture = self.capture, None
        status = capture.stop(archive=False)
        def expected_restart(error):
            if not isinstance(error, dict):
                return False
            stream = error.get('status', {}).get('stream','').split('__')
            return (error.get('pod') or (stream[1] if len(stream)==3 else None)) in self.restart_pods

        unexpected = [error for error in status.get('errors', []) if not expected_restart(error)]
        self.restart_pods.clear()
        if unexpected:
            raise UnsafeReadiness('capture stopped with shared errors: '+str(unexpected[:2]))
        if status.get('cleanup', {}).get('restored') is not True:
            raise UnsafeReadiness('probe instrumentation restoration did not verify')

    def finish(self):
        self._close()

    def _open(self, spec, path):
        from .. import runner as r
        from ..structured_capture import RepairCapture
        from .run import _window_token, capture_clocks

        target = spec['id'] if spec['phase']=='restart' or spec['scenario']=='network' else spec['cell']
        if target==self.group and self.capture is not None:
            return
        self._close()
        settings = dict(r.CONFIG, capture_expected_seconds=1200)
        if spec['scenario']=='gateway':
            settings['gateway_restart_token'] = _window_token(self.ctx, spec)
        # Each reopened capture gets a new attempt directory; adjacent probes
        # in the same live group still reuse the existing capture above.
        capture_path = path if spec['scenario']=='network' else self.root/'captures'/target/spec['id']
        self.capture = RepairCapture(self.driver.cell, capture_path, cutoff(self.ctx)-RESERVE, config=settings).start()
        self.group = target
        clocks = capture_clocks(self.capture, r.CONFIG.get('generator_node_name'))
        self.ctx.write(path/'clock.json', clocks)
        if not clocks.get('valid'):
            raise UnsafeReadiness('readiness clock gate failed')

    def _signals(self, spec, previous=None, replaced_gateway=None):
        """Observe bounded file activity by exact captured Pod and inode."""
        capture = self.capture
        observed = {}
        pods = capture._pods('agent') | capture._pods('gateway')
        for key, pod in pods.items():
            name = pod['metadata']['name']
            uid = pod['metadata']['uid']
            original = capture.pods[key]['metadata']['uid']
            if uid != original and (key != 'gateway' or uid != replaced_gateway):
                raise UnsafeReadiness('unexpected collector replacement: '+name)
            files = ['metrics.json']
            if key.startswith('agent:'):
                files.append('receiver-input.ndjson')
            elif spec['source']=='trace':
                files.append('traces.json')
            for filename in files:
                raw = self.ctx.kube('exec','-n','karthos-system',name,'-c','diag-reader','--',
                                    'sh','-c','if [ -f "$1" ]; then stat -c "%i %s" "$1"; fi',
                                    'readiness','/diag/'+filename, timeout=5).stdout.strip()
                inode, size = (map(int, raw.split()) if raw else (None, 0))
                identity = key+':'+filename
                value = dict(uid=uid, pod=name, inode=inode, bytes=size)
                if previous is not None:
                    before = previous[identity]
                    if before['uid'] == uid and before['bytes'] and (
                            before['inode'] != inode or before['bytes'] > size):
                        raise UnsafeReadiness('collector capture rotated or truncated: '+identity)
                    value['advanced'] = size > (before['bytes'] if before['uid']==uid else 0)
                observed[identity] = value
        return observed

    def _api_baseline(self, spec, path):
        from .. import runner as r

        with r.forward('deployment/karthos-backend','karthos-system',28983,8080) as base:
            at = r.utc()
            params = {'metric_name': REQUEST_COUNT, 'source': source_id(spec['source']),
                      'aggregation': 'latest', 'from': at, 'to': at}
            from .poll_capture import capture
            payload, receipt = capture(self.ctx, path/'api-polls'/'baseline', base, params, time.monotonic()+30)
            self.ctx.write(path/'api-probe-baseline-receipt.json', receipt)
            if payload is None:
                raise ValueError('baseline API capture failed: '+str(receipt))
        baseline = {'query': params, 'response': payload, 'checkout_samples': list(_checkout_samples(payload, spec))}
        self.ctx.write(path/'api-probe-baseline.json', baseline)
        return baseline

    def _api(self, spec, path, start, end, deadline, *, baseline, source_records, checkout_calls):
        from .. import runner as r
        from .setup import verify_versions

        if not checkout_calls:
            raise ValueError('no independently observed checkout completions for the probe')
        with r.forward('deployment/karthos-backend','karthos-system',28983,8080) as base:
            if spec['cell'] not in self.catalogued:
                catalog = r.api_pages(base,'/api/v1/metrics/catalog',{},'metrics',path/'catalog-pages')
                visible = {m['name'] for m in catalog.get('data',{}).get('metrics',[])}
                from .models import definition_path
                from .evaluate import catalog_contract
                definition=read(definition_path(self.ctx,'coverage.yaml'))
                declared={m['name'] for m in definition['entries']}
                contract=catalog_contract(definition,catalog) if definition.get('contract') else None
                self.ctx.write(path/'catalog-visibility.json', {
                    'visible': sorted(visible & declared), 'unavailable': sorted(declared-visible), 'contract':contract})
                if len(declared)!=43 or not visible or (contract is not None and not contract['valid']):
                    raise UnsafeReadiness('public metric catalog unavailable')
                self.catalogued.add(spec['cell'])
            from .poll_capture import capture
            poll = 0
            failed_at_cutoff = False
            # Diagnostic grace never changes a verdict that missed the gate.
            diagnostic_deadline = min(deadline+90, time.monotonic()+max(0, cutoff(self.ctx)-time.time()-RESERVE))
            while time.monotonic() < diagnostic_deadline:
                # An exact start evaluation captures pre-probe values that
                # reached storage after the separate baseline request.
                now = r.utc()
                params = {**baseline['query'], 'from': start, 'to': now}
                failed_at_cutoff = failed_at_cutoff or time.monotonic() >= deadline
                poll += 1
                payload, receipt = capture(self.ctx, path/'api-polls'/f'poll-{poll:04d}', base, params, diagnostic_deadline)
                rejected = []
                samples = []
                if payload is not None:
                    for sample in payload.get('data', {}).get('samples', []):
                        reasons = _sample_rejections(sample, spec)
                        if reasons:
                            rejected.append({'sample':sample, 'reasons':reasons})
                    try:
                        samples = list(_checkout_samples(payload, spec))
                    except ValueError as exc:
                        rejected.append({'reasons':['query-contract'], 'error':str(exc)})
                evidence = _counter_evidence(baseline['checkout_samples'], samples,
                                             source_records.get('checkout_counter_points', []),
                                             stamp(start), stamp(now), rejected)
                api_completed = receipt.get('api_completed_monotonic')
                if api_completed is None:
                    api_completed = time.monotonic()
                failed_at_cutoff = failed_at_cutoff or api_completed >= deadline
                self.ctx.write(path/'api-polls'/f'poll-{poll:04d}.json', {
                    'request': params, 'response': payload, 'receipt': receipt, 'rejections': rejected,
                    'evidence': evidence, 'diagnostic_only':failed_at_cutoff})
                self.ctx.write(path/'api-probe-diagnostics.json', {'polls':poll, 'last_receipt':receipt,
                    'failed_at_cutoff':failed_at_cutoff, 'delayed_activity':bool(evidence and failed_at_cutoff),
                    'gate_deadline_monotonic':deadline, 'diagnostic_deadline_monotonic':diagnostic_deadline})
                if evidence and not failed_at_cutoff:
                    self.ctx.write(path/'extended/api/servicegraph.request.count.json', payload)
                    self.ctx.write(path/'api-probe.json', {
                        'query': params, 'response_query': payload['data']['query'], 'baseline': baseline,
                        'checkout_samples': samples, 'counter_evidence': evidence,
                        'probe_start': start, 'scheduled_end': end, 'checkout_completions': checkout_calls})
                    selection = self.driver._selection(path, spec)
                    self.ctx.write(path/'versions-after.json',verify_versions(self.ctx))
                    return selection
                if evidence and failed_at_cutoff:
                    break
                time.sleep(min(2, max(0, diagnostic_deadline-time.monotonic())))
        raise ValueError('no fresh selected-source checkout counter activity for the probe')

    def _generator(self, spec, path):
        from .. import pilot, runner as r, timing_pilot
        from ..generator_execution import ArtifactExportError, execute_generator
        from .run import _owned_delete, _window_token

        token = _window_token(self.ctx, spec)
        run_id = self.ctx.run_id+':'+spec['id']
        job = timing_pilot.make_job(self.driver.cell, dict(spec, name=spec['id'], kind='confirmation'), run_id)
        pilot.configure_generator_deadline(job, spec['duration_seconds'],
                                           dict(r.CONFIG,telemetry_drain_seconds=0,instrumentation_budget_seconds=0),
                                           r._DEADLINE-time.monotonic(), time.time())
        self.ctx.write(path/'job.json',job)
        self.ctx.write(path/'manifest.json', {'phase':'generator_and_export', 'run_id':run_id})
        objects, state = [], {}
        stop = threading.Event()
        worker = None
        verified = False
        if spec['phase'] in ('resource','restart'):
            if spec['scenario']=='gateway':
                gateway = next(plan for plan in self.capture.plans if plan['kind']=='gateway')
                self.ctx.write(path/'structured/gateway/snapshot.json', gateway)
            worker = threading.Thread(target=self.driver._stimulus,
                                      args=(spec,path,token,stop,state,objects),daemon=True)
            worker.start()
        try:
            result = execute_generator(job, self.driver.cell.namespace, path, run_id,
                                       spec['generator'], 'matched',
                                       export_timeout_seconds=r.CONFIG['artifact_export_seconds'])
            verified = pilot.generator_log_complete(result)
            if not verified or not result.get('job_finished'):
                raise UnsafeReadiness('probe generator evidence or Job termination did not verify')
            records = read(path/'generator-window.json')
            from ..repair_pilot import window_bounds
            ledger = [json.loads(line) for line in (path/'ledger.ndjson').read_text().splitlines() if line]
            bounds = window_bounds(records[0]['started_at'], records[-1]['ended_at'],
                                   spec['duration_seconds'], ledger)
            if worker:
                worker.join(timeout=max(0,min(90, r._DEADLINE-time.monotonic())))
                if worker.is_alive():
                    raise UnsafeReadiness('diagnostic worker exceeded the readiness deadline')
                if state.get('state')!='complete':
                    raise ValueError(state.get('error','diagnostic mechanism did not complete'))
            good = [row for row in ledger if type(row.get('status')) is int and 200<=row['status']<400]
            trace_ids = sorted({row['trace_id'] for row in ledger
                                if re.fullmatch('[a-f0-9]{32}',row.get('trace_id') or '')})
            checkout = [row for row in ledger if row.get('route')=='checkout'
                        and type(row.get('status')) is int and 100<=row['status']<600]
            checkout_trace_ids = sorted({row['trace_id'] for row in checkout
                                         if re.fullmatch('[a-f0-9]{32}',row.get('trace_id') or '')})
            self.ctx.write(path/'workload-observation.json', {
                'run_id':run_id, 'bounds':bounds, 'completed':len(ledger), 'healthy_responses':len(good),
                'unexpected_outcomes':sum(row.get('outcome_ok') is False for row in ledger),
                'workload_exit_code':result['workload_exit_code'], 'trace_ids':trace_ids,
                'checkout_completions':len(checkout), 'checkout_trace_ids':checkout_trace_ids})
            healthy_checkout = any(row.get('route')=='checkout' and row.get('outcome_ok') is True for row in good)
            if not good or (spec['phase']!='restart' and
                            (not healthy_checkout or result.get('workload_exit_code')!=0)):
                raise ValueError('matched probe did not complete the expected application journey')
            self.ctx.write(path/'manifest.json',dict(bounds,phase='complete',run_id=run_id,
                                                     generator_artifacts_complete=True))
            return bounds, state, len(good), checkout_trace_ids, len(checkout)
        except ArtifactExportError as exc:
            raise UnsafeReadiness('probe generator artifact export failed: '+str(exc)) from exc
        finally:
            stop.set()
            if worker and worker.is_alive():
                worker.join(timeout=max(0,min(5,r._DEADLINE-time.monotonic())))
            if worker and worker.is_alive():
                raise UnsafeReadiness('diagnostic worker still active; cluster teardown required')
            try:
                for obj in reversed(objects):
                    meta = obj['metadata']
                    _owned_delete(self.ctx,obj['kind'],meta['name'],meta.get('namespace'),
                                  'gke-qualification-v1',token,cluster=obj['kind']=='StorageClass')
                pilot.cleanup_generator(job['metadata']['name'],self.driver.cell.namespace,path,verified)
            except Exception as exc:
                raise UnsafeReadiness('probe cleanup failed: '+str(exc)) from exc

    def check(self, spec):
        from ..structured_capture import CapturePodsPending
        from .timing import timed

        path = self.root/'cases'/spec['id']
        path.mkdir(parents=True, exist_ok=False)
        started = time.monotonic()
        if spec['cell'] in self.broken_cells:
            return {'state':'blocked by dependency','reason':'cell setup failed'}
        if spec['phase']=='restart' and spec['cell'] in self.unrecovered_restart_cells:
            return {'state':'blocked by dependency','reason':'previous restart recovery not verified in this cell'}
        setup_finished = started
        self.ctx.event('readiness-probe-start',case=spec['id'])
        if self.cell!=spec['cell']:
            self._close()
            try:
                with timed(self.ctx,'readiness-cell-setup',spec['id']):
                    self.driver.setup(spec, parent=self.root/'setup')
            except Exception as exc:
                if any(term in str(exc).lower() for term in ('ownership','unowned','uid changed','watchdog','deadline','cleanup')):
                    raise UnsafeReadiness('shared setup safety gate: '+str(exc)) from exc
                self.broken_cells.add(spec['cell'])
                raise
            self.cell = spec['cell']
            setup_finished = time.monotonic()
            self.setup_seconds = max(self.setup_seconds, setup_finished-started)
        try:
            with timed(self.ctx,'readiness-capture',spec['id']):
                self._open(spec,path)
                before = self._signals(spec)
        except Exception as exc:
            raise UnsafeReadiness('shared capture setup failed: '+str(exc)) from exc
        with timed(self.ctx,'readiness-api-baseline',spec['id']):
            baseline = self._api_baseline(spec,path)
        with timed(self.ctx,'readiness-load',spec['id']):
            bounds, diagnostic, completed, trace_ids, checkout_calls = self._generator(spec,path)

        if spec['phase']=='restart':
            self.restart_pods = {pod['metadata']['name'] for pod in diagnostic['pods_before']['items']}
        replacement = None
        if spec['scenario']=='gateway':
            current = self.capture._pods('gateway')['gateway']
            replacement = current['metadata']['uid']
            observed = {pod['metadata']['uid'] for pod in diagnostic['pods_after']['items']
                        if not pod['metadata'].get('deletionTimestamp')}
            if replacement not in observed or replacement==self.capture.pods['gateway']['metadata']['uid']:
                raise UnsafeReadiness('Gateway replacement did not match observed owned restart')

        deadline = time.monotonic()+45
        source_records = {}
        while True:
            try:
                after = self._signals(spec,before,replacement)
                if after and all(row['advanced'] for row in after.values()):
                    source_records = self._source_activity(spec, before, after, trace_ids)
                    if source_records:
                        points = self._checkout_points(spec, before, after, bounds['start'])
                        source_records['checkout_counter_points'] = points
                        if points:
                            break
            except CapturePodsPending:
                after = {}
            except UnsafeReadiness:
                raise
            except Exception as exc:
                raise UnsafeReadiness('required collector capture read failed: '+str(exc)) from exc
            if time.monotonic() >= deadline:
                if source_records:
                    break
                raise UnsafeReadiness('required collector output did not advance')
            time.sleep(1)
        self.ctx.write(path/'collector-activity.json',{'before':before,'after':after,'source_records':source_records})
        self._sampler_health(spec, diagnostic, path)
        with timed(self.ctx,'readiness-api',spec['id']):
            selection = self._api(spec,path,bounds['start'],bounds['end'],time.monotonic()+30,
                                  baseline=baseline,source_records=source_records,checkout_calls=checkout_calls)
        if selection.get('state')!='pass':
            return {'state':'failed','reason':'source selection evidence: '+str(selection)}
        if spec['scenario']=='replanned-trace':
            transition = self.driver.setup_path/'arbitration-transition.json'
            if not transition.exists():
                return {'state':'blocked by dependency','reason':'explicit rediscovery and redeployment missing'}
            self.ctx.write(path/'arbitration-transition.json', read(transition))

        if spec['phase']=='resource':
            self._resource_evidence(spec, path, diagnostic)
        if spec['scenario']=='network':
            try:
                self._archive_network(path)
            except Exception as exc:
                raise UnsafeReadiness('network production capture failed: '+str(exc)) from exc
        elapsed = time.monotonic()-started
        # The short probes omit numerical verification. Prior accepted windows
        # supply a conservative full-workflow floor for each workload length.
        return {'state':'passed','completed_requests':completed,'elapsed_seconds':elapsed,
                'probe_seconds':time.monotonic()-setup_finished, 'setup_seconds':self.setup_seconds,
                'preflight_seconds':self.preflight_seconds}

    def _source_activity(self, spec, before, after, trace_ids):
        def contains(key, patterns):
            current, previous = after[key], before[key]
            offset = previous['bytes'] if previous['uid']==current['uid'] else 0
            # Read only appended bytes with a bounded matched token result.
            text = self.ctx.kube('exec','-n','karthos-system',current['pod'],'-c','diag-reader','--','sh','-c',
                'tail -c +"$1" "$2" | grep -a -E -o -m 1 "$3" | head -n 1',
                'readiness',str(offset+1),'/diag/'+key.rsplit(':',1)[1],patterns,timeout=12).stdout.strip()
            return text

        if spec['source']=='trace':
            if not trace_ids:
                raise ValueError('no generator trace IDs available for correlation')
            matched = contains('gateway:traces.json', '|'.join(trace_ids[:256]))
            metric = contains('gateway:metrics.json', 'traces_service_graph_request_total')
            return {'gateway_trace_id':matched, 'gateway_metric':metric} if matched and metric else {}
        app_nodes = [node['name'] for node in self.driver.nodes if node['slot']!='infra']
        agents = {node:contains('agent:'+node+':metrics.json','istio_requests_total') for node in app_nodes}
        gateway = contains('gateway:metrics.json','istio_requests_total')
        return {'agent_metrics':agents, 'gateway_metric':gateway} if gateway and all(agents.values()) else {}

    def _checkout_points(self, spec, before, after, start):
        from ..evidence_v4 import attrs

        current, previous = after['gateway:metrics.json'], before['gateway:metrics.json']
        offset = previous['bytes'] if previous['uid']==current['uid'] else 0
        name = RAW_REQUEST_COUNT[spec['source']]
        result = self.ctx.kube(
            'exec','-n','karthos-system',current['pod'],'-c','diag-reader','--','sh','-c',
            'test "$(stat -c %i "$2")" = "$5" || exit 1; '
            'tail -c +"$1" "$2" | head -c "$4" | grep -aF -- "$3" | gzip -c; '
            'test "$(stat -c %i "$2")" = "$5"',
            'readiness',str(offset+1),'/diag/metrics.json',name,str(current['bytes']-offset),
            str(current['inode']),timeout=12,binary=True)
        points = []
        earliest = stamp(start)
        # Bound transport to the observed append interval and decompress
        # incrementally, since a single exporter batch can include other metrics.
        with gzip.GzipFile(fileobj=io.BytesIO(result.stdout)) as stream:
            for line in stream:
                try:
                    document = json.loads(line)
                except json.JSONDecodeError:
                    # An offset can land inside an in-progress exporter line.
                    continue
                for group in document.get('resourceMetrics', []):
                    for scope in group.get('scopeMetrics', []):
                        for metric in scope.get('metrics', []):
                            counter = metric.get('sum', {})
                            if (metric.get('name') != name or counter.get('isMonotonic') is not True
                                    or counter.get('aggregationTemporality') not in (2, 'AGGREGATION_TEMPORALITY_CUMULATIVE')):
                                continue
                            for point in counter.get('dataPoints', []):
                                labels = attrs(point.get('attributes', []))
                                if (labels.get('client') != 'frontend' or labels.get('server') != CHECKOUT[spec['demo']]
                                        or labels.get('karthos_source') != source_id(spec['source'])):
                                    continue
                                at = int(point.get('timeUnixNano', 0))/1e9
                                reported = point.get('asInt', point.get('asDouble'))
                                if reported is None or int(point.get('flags', 0)) & 1:
                                    continue
                                value = float(reported)
                                if at >= earliest and value > 0 and math.isfinite(value):
                                    epoch = int(point.get('startTimeUnixNano', 0))/1e9
                                    points.append({'timestamp': at, 'started_at': epoch or None,
                                                   'value': value, 'labels': labels})
        return points

    def _resource_evidence(self, spec, path, diagnostic):
        helpers = diagnostic.get('helper_lifecycles', [])
        expected = {node['name'] for node in self.driver.nodes if node['slot']!='infra'}
        if {row.get('node_name') for row in helpers} != expected or any(not row.get('uid') for row in helpers):
            raise ValueError('both exact resource helper node identities required')
        evidence = {'helper_lifecycles':helpers}
        if spec['scenario']=='storage':
            samples, bytes_by_pod = {}, {}
            for row in helpers:
                rows = [json.loads(line) for line in
                        (path/'references/volumes'/(row['pod']+'.ndjson')).read_text().splitlines() if line]
                valid = [sample for sample in rows if sample.get('observer_pod_uid')==row['uid']
                         and sample.get('node')==row['node_name'] and sample.get('source')=='statvfs'
                         and sample.get('dimensions', {}).get('mount')=='/owned'
                         and sample.get('dimensions', {}).get('device')]
                if not valid:
                    raise ValueError('PVC mount reference missing: '+row['pod'])
                samples[row['pod']] = valid[-1]
                fio = read(path/'stimulus-logs'/(row['pod']+'__stimulus.log'))
                total = sum(job[direction]['io_bytes'] for job in fio['jobs'] for direction in ('read','write'))
                if total<=0 or any(job['error'] for job in fio['jobs']):
                    raise ValueError('PVC I/O stimulus did not record successful operations: '+row['pod'])
                bytes_by_pod[row['pod']] = total
            evidence.update(volume_samples=samples, io_bytes_by_pod=bytes_by_pod)
        if spec['scenario']=='cpu-memory':
            samples = _cpu_memory_samples(self.capture.capture,helpers)
            if len(samples)!=len(helpers):
                raise ValueError('kubelet did not observe CPU and memory for every resource helper')
            evidence['kubelet_samples'] = samples
        if spec['scenario']=='network':
            evidence['network_target'] = read(path/'stimulus-network-target.json')
        self.ctx.write(path/'resource-observation.json',evidence)

    def _sampler_health(self, spec, diagnostic, path):
        fast = self.capture.capture
        expected_old = set()
        if spec['phase']=='restart':
            expected_old = {pod['metadata']['name'] for pod in diagnostic.get('pods_before',{}).get('items',[])}
        sampling_errors = list(fast.sampling_errors)
        errors = [row for row in sampling_errors
                  if not isinstance(row, dict) or row.get('pod') not in expected_old]
        logs = [dict(poller.status(), pod=poller.pod,
                     namespace=getattr(poller, 'namespace', None),
                     container=getattr(poller, 'container', None))
                for poller in fast.log_captures]
        unhealthy_logs = [state for state in logs if state['pod'] not in expected_old
                          and (state.get('errors') or state.get('exited_before_stop'))]
        threads = [{'name':getattr(thread, 'name', None),
                    'ident':getattr(thread, 'ident', None), 'alive':thread.is_alive()}
                   for thread in fast.sampler_threads]
        report = {'observed_at':time.time(), 'case':spec.get('id'),
                  'excluded_expected_restart_pods':sorted(expected_old),
                  'sampling_errors':sampling_errors, 'unexpected_sampling_errors':errors,
                  'logs':logs, 'unhealthy_logs':unhealthy_logs, 'sampler_threads':threads}
        try:
            report['streams'] = fast._sampling_status()['streams']
        except Exception as exc:
            report['streams'] = {}
            report['sampling_status_error'] = {'type':type(exc).__name__, 'message':str(exc)}
        report['valid'] = not (errors or unhealthy_logs or not threads
                               or any(not row['alive'] for row in threads)
                               or report.get('sampling_status_error'))
        # Failed cases use the same compact export as successful cases. Persist
        # the exact failure boundary before raising, so teardown cannot erase
        # the only record of which sampler, log stream or worker failed.
        self.ctx.write(path/'capture-health.json', report)
        if not report['valid']:
            raise UnsafeReadiness('shared sampler or log capture failed')

    def _archive_network(self, path):
        capture, self.capture = self.capture, None
        self.driver.paths[path.name] = path
        status = capture.stop()
        if not status.get('valid') or not status.get('cleanup', {}).get('restored'):
            raise UnsafeReadiness('network full-window raw capture did not verify')

    def publish_case(self, spec, verdict):
        path = self.root/'cases'/spec['id']
        path.mkdir(parents=True, exist_ok=True)
        self.ctx.write(path/'receipt.json',verdict)
        if spec['scenario']=='network' and spec['id'] in self.driver.paths:
            return self.driver.upload(spec, verdict, evidence_prefix='readiness/cases/'+spec['id'])
        # The compact receipt retains useful evidence without repacking all
        # captured streams for every lightweight mechanism.
        compact = self.root/'receipts'/spec['id']
        compact.mkdir(parents=True, exist_ok=False)
        names = ['clock.json','collector-activity.json','source-selection.json','api-probe.json',
                 'scenario-result.json','catalog-visibility.json','capture-health.json',
                 'generator-artifact-status.json','arbitration-transition.json','api-probe-baseline.json',
                 'api-probe-baseline-receipt.json','api-probe-diagnostics.json',
                 'workload-observation.json','resource-observation.json']
        if spec['phase']=='arbitration':
            names.extend(str(item.relative_to(path)) for item in sorted((path/'selection').glob('*.json')))
        import shutil
        if (path/'api-polls').is_dir():
            shutil.copytree(path/'api-polls', compact/'api-polls')
        self.ctx.write(compact/'receipt.json', {
            'run_id':self.ctx.run_id, 'case':spec, 'verdict':verdict,
            'evidence':{name:read(path/name) for name in names if (path/name).is_file()}})
        return self.publish(compact, 'cases/'+spec['id'])

    def publish(self, path, relative='matrix'):
        return export.upload(self.ctx,path,bucket=self.ctx.state['terraform']['foundation']['evidence_bucket'],
                             prefix='gke-qualification-v1/runs/'+self.ctx.run_id+'/readiness/'+relative)

    def inventory(self):
        return {kind:{item['metadata']['name']:item for item in self.ctx.json('get',kind).get('items',[])}
                for kind in ('Namespace','PersistentVolume','StorageClass','MutatingWebhookConfiguration',
                             'ValidatingWebhookConfiguration','ClusterRoleBinding','ClusterRole','CustomResourceDefinition')}

    def provider_disks(self):
        from .provision import doc, OWNER
        disks = doc(self.ctx,['gcloud','compute','disks','list','--project='+self.ctx.config['gcp']['project_id'],
                            '--filter=labels.evaluation-owner='+OWNER+' AND labels.evaluation-run='+self.ctx.run_id,
                            '--format=json'])
        for disk in disks:
            labels = disk.get('labels', {})
            if labels.get('evaluation-owner')!=OWNER or labels.get('evaluation-run')!=self.ctx.run_id:
                raise UnsafeReadiness('provider disk listing contains foreign resources')
        return {disk['selfLink']:str(disk['id']) for disk in disks}

    def reset(self):
        from .provision import OWNER
        from .setup import verify_versions
        from .run import _owned_delete

        began = time.monotonic()
        current = self.inventory()
        for kind, entries in current.items():
            for name, obj in entries.items():
                if name in self.initial[kind] or kind=='PersistentVolume':
                    continue
                _owned_delete(self.ctx, kind, name, None, OWNER, self.ctx.run_id, cluster=True,
                              expected_uid=obj['metadata']['uid'])
        # Claim-owned PVs and their cloud disks must be reclaimed by the CSI
        # driver. Never delete an unlabelled or rebound volume by name.
        deadline = min(cutoff(self.ctx)-RESERVE, time.time()+150)
        while True:
            final = self.inventory()
            disks = self.provider_disks()
            changes = [kind for kind in current if
                       {name:obj['metadata']['uid'] for name,obj in self.initial[kind].items()} !=
                       {name:obj['metadata']['uid'] for name,obj in final[kind].items()}]
            if not changes and disks == self.initial_disks:
                break
            if time.time()>=deadline:
                raise UnsafeReadiness('reset inventory differs: '+str(changes)+'; provider disks equal='+str(disks==self.initial_disks))
            time.sleep(2)
        versions = verify_versions(self.ctx)
        host = self._host_baseline()
        after_baseline = self.inventory()
        if any({name:obj['metadata']['uid'] for name,obj in after_baseline[kind].items()} !=
               {name:obj['metadata']['uid'] for name,obj in self.initial[kind].items()} for kind in self.initial):
            raise UnsafeReadiness('baseline helper did not leave the cluster clean')
        self.ctx.write(self.root/'reset/host-baseline.json',host)
        return {'valid':True,'seconds':time.monotonic()-began,'versions':versions,
                'baseline':'reset/host-baseline.json'}

    def _host_baseline(self):
        from .provision import OWNER
        from .run import _owned_delete, _window_token

        token = _window_token(self.ctx, {'id':'readiness-handoff-host-baseline'})
        namespace = 'eval-'+token
        label = {'evaluation.karthos.dev/owner':OWNER,'evaluation.karthos.dev/run-id':self.ctx.run_id}
        spec = {'apiVersion':'v1','kind':'Namespace','metadata':{'name':namespace,'labels':label}}
        response = self.ctx.kube('create','-f','-','-o','json',input_text=json.dumps(spec))
        uid = json.loads(response.stdout)['metadata']['uid']
        try:
            _, pods = self.driver._reference_start(namespace,token,self.root/'reset/reference')
            command = (
                "import json,os,sys;sys.path.insert(0,'/opt/evaluation');"
                "from infrastructure.helpers.reference_sampler import sample_once;"
                "node=os.environ['NODE_NAME'];"
                "rows=sample_once(node=node,node_uid=json.loads(os.environ['NODE_UID_MAP'])[node],"
                "pod_uid=os.environ['POD_UID'],run_id=os.environ['RUN_ID']);"
                "print(json.dumps(rows))"
            )
            baseline = {}
            for node in self.driver.nodes:
                name = node['name']
                pod = pods[name]['metadata']
                raw = self.ctx.kube('exec','-n',namespace,pod['name'],'-c','reference','--',
                                    'python3','-c',command,timeout=20).stdout
                rows = json.loads(raw)
                if (not {'cpu.time','disk.sectors','network.bytes'} <= {row.get('metric') for row in rows}
                        or any(row.get('node')!=name or row.get('node_uid')!=node['uid']
                               or row.get('observer_pod_uid')!=pod['uid'] or row.get('run_id')!=token for row in rows)):
                    raise UnsafeReadiness('fresh cumulative host baseline incomplete for '+name)
                baseline[name] = rows
            return baseline
        finally:
            _owned_delete(self.ctx,'Namespace',namespace,None,OWNER,self.ctx.run_id,cluster=True,expected_uid=uid)

    def commit(self, summary):
        from .provision import commit_readiness_handoff
        summary['reset']['export'] = self.publish(self.root/'reset','reset')
        if not summary['reset']['export'].get('verified'):
            raise UnsafeReadiness('reset evidence readback missing')
        path = self.root/'handoff'
        self.ctx.write(path/'forecast.json', {key:summary[key] for key in ('run_id','matrix_export','reset','forecast_seconds','commit_before')})
        summary['handoff_export'] = self.publish(path,'handoff')
        if not summary['handoff_export'].get('verified'):
            raise UnsafeReadiness('readiness handoff readback missing')
        if self.ctx.config.get('execution_scope') == 'readiness-only':
            summary['watchdog_handoff'] = {'requested':False,'reason':'readiness-only execution; measured traffic forbidden'}
        else:
            summary['watchdog_handoff'] = commit_readiness_handoff(self.ctx, summary['commit_before'])




def run(ctx, driver):
    from .readiness import budget, execute

    probes = ProbeDriver(driver)

    def check(spec):
        if probes.initial is None:
            try:
                probes.start()
            except Exception as exc:
                raise UnsafeReadiness('shared cluster preflight failed: '+str(exc)) from exc
        started = time.monotonic()
        try:
            verdict = probes.check(spec)
        except (UnsafeReadiness, KeyboardInterrupt) as exc:
            verdict = {'state':'failed','reason':str(exc) or type(exc).__name__,'unsafe':True}
        except Exception as exc:
            verdict = {'state':'failed','reason':str(exc)}
        if spec['phase']=='restart' and verdict['state']=='failed':
            scenario = probes.root/'cases'/spec['id']/'scenario-result.json'
            if not scenario.is_file() or read(scenario).get('state')!='complete':
                probes.unrecovered_restart_cells.add(spec['cell'])
        try:
            if (spec['scenario']=='network' and probes.capture is not None
                    and probes.capture.path==probes.root/'cases'/spec['id']):
                try:
                    probes._archive_network(probes.capture.path)
                except Exception as exc:
                    verdict.update(state='failed',unsafe=True,archive_error=str(exc))
            receipt = probes.publish_case(spec,verdict)
            if receipt.get('verified') is not True:
                raise ValueError('probe receipt readback not verified')
        except Exception as exc:
            raise UnsafeReadiness('readiness evidence export failed: '+str(exc)) from exc
        ctx.event('readiness-probe-exported',case=spec['id'],verdict=verdict['state'])
        elapsed = time.monotonic()-started
        # Archived full-window time includes deployment. The current probe's
        # observed setup is budgeted independently. Credit only the confirmed
        # fraction of the archived projection phase, not transfer or capture.
        replay = ctx.manifest['readiness_benchmark']
        reduction = min(1-statistics.median(
            trial['candidate']['timings']['projection']['seconds'] /
            trial['baseline']['timings']['projection']['seconds']
            for trial in replay['cases'][name]['trials'])
            for name in ('astronomy','boutique'))
        historic = 1698 if spec['duration_seconds']==60 else 2699-max(0,reduction)*520
        if verdict['state']=='passed':
            verdict['archived_total_seconds'] = historic
            verdict['forecast_seconds'] = verdict['probe_seconds']+elapsed-verdict['elapsed_seconds']
        return dict(verdict, export=receipt)

    with budget(ctx):
        return execute(ctx,check,probes.reset,probes.publish,finish=probes.finish,commit=probes.commit)
