"""Bounded Astronomy TCP diagnostic. Importing this module performs no live work.

Parent driver owns traffic and invokes PacketCapture(path, deadline_epoch,
watchdog_image).start()/stop(planned_load_end_epoch=...), or uses it as a
context manager after assigning planned_load_end_epoch. Missing load bounds keep
coverage incomplete. The watchdog
image must be an immutable Python3 image (the pinned Astronomy load-generator
image is suitable). Evidence describes packet capture only, not resource accuracy.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
import tarfile
import time
import uuid
from pathlib import Path
from datetime import datetime

from evaluation import capture_watchdog as watchdog

NAMESPACE = 'karthos-eval-astronomy'
TARGETS = ('frontend', 'frontend-proxy')
NETSHOOT_IMAGE = 'nicolaka/netshoot@sha256:7f08c4aff13ff61a35d30e30c5c1ea8396eac6ab4ce19fd02d5a4b3b5d0d09a2'
MAX_BYTES = 32 * 1024 * 1024
PACKET_LIMIT = 50000
CAPTURE_SCRIPT = r'''set -u
mkdir -p /capture
(tcpdump --version; timeout --version; uname -a) > /capture/versions.txt 2>&1
date -u +%Y-%m-%dT%H:%M:%SZ > /capture/started-at.txt
set +e
timeout -s INT -k 5s 600s tcpdump -p -s 512 -c 50000 --time-stamp-precision=nano -U -i any -w /capture/traffic.pcap 'tcp and (port 8080 or port 15006)' > /capture/stdout.txt 2> /capture/stderr.txt &
pid=$!
trap 'kill -INT "$pid" 2>/dev/null || true' TERM INT
# Do not admit traffic before tcpdump opened its output. Privilege setup errors
# must leave readiness false rather than silently running an empty diagnostic.
for n in $(seq 1 10); do
  if test -e /capture/traffic.pcap && kill -0 "$pid" 2>/dev/null; then touch /capture/READY; break; fi
  kill -0 "$pid" 2>/dev/null || break
  sleep 1
done
while kill -0 "$pid" 2>/dev/null; do
  if [ -e /capture/STOP ]; then kill -INT "$pid" 2>/dev/null || true; break; fi
  sleep 1
done
wait "$pid"
code=$?
printf '%s\n' "$code" > /capture/exit-code.txt
date -u +%Y-%m-%dT%H:%M:%SZ > /capture/finished-at.txt
touch /capture/DONE
# Preserve closed files until normal cleanup or the independent watchdog restores.
while :; do sleep 30; done
'''


def coverage_status(stderr, finished_epoch, planned_load_end_epoch):
    matches = re.findall(r'^([0-9]+) packets? captured\s*$', stderr, re.MULTILINE)
    count = int(matches[-1]) if matches else None
    drops = re.findall(r'^([0-9]+) packets? dropped by kernel\s*$', stderr, re.MULTILINE)
    errors = []
    if count is None:
        errors.append('missing tcpdump packet-count evidence')
    elif count >= PACKET_LIMIT:
        errors.append('packet count cap reached; coverage incomplete')
    if drops and int(drops[-1]) > 0:
        errors.append('kernel packet drops; coverage incomplete')
    if planned_load_end_epoch is None:
        errors.append('planned load end unavailable; coverage incomplete')
    elif finished_epoch < planned_load_end_epoch:
        errors.append('capture finished before planned load end')
    return {'valid': not errors, 'state': 'incomplete' if errors else 'pass',
            'captured_packets': count, 'packet_limit': PACKET_LIMIT,
            'finished_at_epoch': finished_epoch, 'planned_load_end_epoch': planned_load_end_epoch,
            'kernel_drops': int(drops[-1]) if drops else None, 'errors': errors}


def application_plan(workload, owner, dummy):
    name = workload['metadata']['name']
    if name not in TARGETS or workload['metadata'].get('namespace', NAMESPACE) != NAMESPACE:
        raise ValueError('packet capture is restricted to Astronomy frontend deployments')
    if workload['spec'].get('replicas', 1) != 1:
        raise ValueError('packet diagnostic requires one replica per deployment')
    after = copy.deepcopy(workload)
    template = after['spec']['template']
    spec = template['spec']
    if watchdog.OWNER in template.get('metadata', {}).get('annotations', {}):
        raise ValueError('existing capture owner')
    if any(c['name'].startswith('diag-') for c in spec['containers']) or any(v['name'] == 'packet-capture' for v in spec.get('volumes', [])):
        raise ValueError('existing diagnostic container or volume')
    template.setdefault('metadata', {}).setdefault('annotations', {})[watchdog.OWNER] = owner
    spec.setdefault('volumes', []).append({'name': 'packet-capture', 'emptyDir': {'sizeLimit': '32Mi'}})
    spec['containers'].append({
        'name': 'diag-tcpdump', 'image': NETSHOOT_IMAGE,
        'command': ['sh', '-c', CAPTURE_SCRIPT],
        'securityContext': {'runAsUser': 0, 'runAsNonRoot': False, 'allowPrivilegeEscalation': False,
                            'capabilities': {'drop': ['ALL'], 'add': ['NET_RAW']}},
        'resources': {'requests': {'cpu': '10m', 'memory': '32Mi'}, 'limits': {'cpu': '250m', 'memory': '96Mi'}},
        'volumeMounts': [{'name': 'packet-capture', 'mountPath': '/capture'}],
        'readinessProbe': {'exec': {'command': ['sh', '-c', 'test -e /capture/READY']}, 'periodSeconds': 1}})
    return {'kind': name, 'owner': owner, 'before_workload': copy.deepcopy(workload), 'after_workload': after,
            'before_configmap': copy.deepcopy(dummy), 'after_configmap': copy.deepcopy(dummy)}


def watchdog_objects(snapshot, image, clock=None):
    if '@sha256:' not in image:
        raise ValueError('watchdog Python image must be pinned by digest')
    name, ns, owner = snapshot['name'], snapshot['namespace'], snapshot['owner']
    if ns != NAMESPACE:
        raise ValueError('unsupported packet namespace')
    labels = {watchdog.OWNER: owner}
    def meta(): return {'name': name, 'namespace': ns, 'labels': labels}
    dummy = snapshot['plans'][0]['before_configmap']['metadata']['name']
    data = {'snapshot.json': json.dumps(snapshot), 'capture_watchdog.py': Path(watchdog.__file__).read_text()}
    if len(json.dumps(data).encode()) > 900 * 1024:
        raise ValueError('watchdog snapshot exceeds ConfigMap bound')
    return [
        {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': meta(), 'data': data},
        {'apiVersion': 'v1', 'kind': 'ServiceAccount', 'metadata': meta()},
        {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'Role', 'metadata': meta(), 'rules': [
            {'apiGroups': ['apps'], 'resources': ['deployments'], 'resourceNames': list(TARGETS), 'verbs': ['get', 'patch']},
            {'apiGroups': [''], 'resources': ['configmaps'], 'resourceNames': [dummy, name], 'verbs': ['get', 'patch']},
            {'apiGroups': [''], 'resources': ['configmaps'], 'resourceNames': ['karthos-evaluation-owner'], 'verbs': ['get']}]},
        {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'RoleBinding', 'metadata': meta(),
         'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Role', 'name': name},
         'subjects': [{'kind': 'ServiceAccount', 'name': name, 'namespace': ns}]},
        {'apiVersion': 'batch/v1', 'kind': 'Job', 'metadata': meta(), 'spec': {
            'backoffLimit': 0, 'activeDeadlineSeconds': max(60, int(snapshot['cleanup_deadline_epoch'] - (time.time() if clock is None else clock)) + 300),
            'template': {'metadata': {'labels': labels, 'annotations': {'sidecar.istio.io/inject': 'false'}}, 'spec': {
                'serviceAccountName': name, 'restartPolicy': 'Never', 'containers': [{
                    'name': 'watchdog', 'image': image, 'command': ['python3', '/scripts/capture_watchdog.py'],
                    'env': [{'name': 'OTEL_TRACES_EXPORTER', 'value': 'none'}],
                    'resources': {'requests': {'cpu': '5m', 'memory': '32Mi'}, 'limits': {'cpu': '100m', 'memory': '128Mi'}},
                    'volumeMounts': [{'name': 'scripts', 'mountPath': '/scripts', 'readOnly': True}]}],
                'volumes': [{'name': 'scripts', 'configMap': {'name': name}}]}}}}]


class PacketCapture:
    def __init__(self, path, deadline_epoch, watchdog_image, *, kube=None):
        self.path = Path(path)
        if deadline_epoch <= time.time():
            raise ValueError('packet diagnostic deadline already passed')
        if '@sha256:' not in watchdog_image:
            raise ValueError('watchdog image must be immutable')
        self.deadline, self.image = deadline_epoch, watchdog_image
        self.owner = uuid.uuid4().hex[:16]
        self.name = 'eval-packets-' + self.owner
        self.dummy_name = self.name + '-guard'
        self.kube = kube or self._kubectl
        self.plans, self.pods, self.created = [], {}, []
        self.snapshot, self.stopped = None, False
        self.planned_load_end_epoch = None
        self.state = {'owner': self.owner, 'valid': False, 'errors': [], 'artifacts': {}, 'evidence_scope': 'TCP diagnostic; no full resource accuracy claim', 'capture_limits': {'seconds': 600, 'packets': PACKET_LIMIT, 'snaplen': 512, 'pod_bytes': MAX_BYTES, 'export_attempts': 3}}

    @staticmethod
    def _kubectl(args, *, input_text=None, output=None, timeout=90):
        return subprocess.run(['kubectl', *args], input=input_text, text=output is None,
                              stdout=output or subprocess.PIPE, stderr=subprocess.PIPE,
                              check=True, timeout=timeout)

    def _call(self, *args, **kwargs):
        return self.kube(list(args), **kwargs)

    def _get(self, resource):
        return json.loads(self._call('get', resource, '-n', NAMESPACE, '-o', 'json').stdout)

    def _write(self, name, value):
        destination = self.path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(value, indent=2) + '\n')

    def _create(self, obj):
        # Track attempted creations as well: timeout can follow a successful API write.
        self.created.append((obj['kind'].lower(), obj['metadata']['name']))
        self._call('create', '-f', '-', input_text=json.dumps(obj))

    def _wait_watchdog(self):
        # A Job create response can precede its Pod. Selector-based wait fails
        # immediately on an empty result; poll existence within one 120s budget.
        until = time.monotonic() + min(120, max(0, self.deadline - time.time()))
        while time.monotonic() < until:
            remaining = until - time.monotonic()
            result = self._call('get', 'pods', '-n', NAMESPACE, '-l', 'job-name=' + self.name,
                                '-o', 'json', timeout=max(1, min(15, remaining)))
            pods = [p for p in json.loads(result.stdout)['items'] if not p['metadata'].get('deletionTimestamp')]
            if len(pods) > 1:
                raise ValueError('ambiguous watchdog pods')
            if pods:
                if pods[0].get('status', {}).get('phase') in ('Failed', 'Succeeded'):
                    raise ValueError('watchdog exited before application mutation')
                remaining = int(until - time.monotonic())
                if remaining <= 0:
                    break
                self._call('wait', '--for=condition=Ready', 'pod/' + pods[0]['metadata']['name'],
                           '-n', NAMESPACE, '--timeout=' + str(remaining) + 's', timeout=remaining + 2)
                return
            time.sleep(min(1, max(0, until - time.monotonic())))
        raise TimeoutError('watchdog pod unavailable within startup budget')

    @staticmethod
    def _installation_patch(plan):
        before, after = plan['before_workload'], plan['after_workload']
        return [{'op': 'test', 'path': '/metadata/uid', 'value': before['metadata']['uid']},
                {'op': 'test', 'path': '/metadata/resourceVersion', 'value': before['metadata']['resourceVersion']},
                {'op': 'test', 'path': '/spec/template', 'value': before['spec']['template']},
                {'op': 'replace', 'path': '/spec/template', 'value': after['spec']['template']}]

    def start(self):
        self.path.mkdir(parents=True, exist_ok=False)
        try:
            namespace = json.loads(self._call('get', 'namespace', NAMESPACE, '-o', 'json').stdout)
            if namespace.get('metadata', {}).get('labels', {}).get('evaluation.karthos.dev/owner') != 'karthos-evaluation':
                raise ValueError('namespace evaluation ownership is missing or changed')
            workloads = [self._get('deployment/' + n) for n in TARGETS]
            marker = json.loads(self._call('get', 'configmap/karthos-evaluation-owner', '-n', NAMESPACE, '--ignore-not-found', '-o', 'json').stdout or 'null')
            if marker is None:
                self._create({'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': 'karthos-evaluation-owner', 'namespace': NAMESPACE, 'labels': {watchdog.OWNER: self.owner}}, 'data': {'scope': 'Astronomy packet diagnostic'}})
                marker = self._get('configmap/karthos-evaluation-owner')
            dummy = {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': self.dummy_name, 'namespace': NAMESPACE, 'labels': {watchdog.OWNER: self.owner}}, 'data': {'unchanged': self.owner}}
            self._create(dummy)
            dummy = self._get('configmap/' + self.dummy_name)
            self.plans = [application_plan(w, self.owner, dummy) for w in workloads]
            # Admission may add default fields. Preserve the admitted template in
            # the durable watchdog snapshot before any application mutation.
            for plan in self.plans:
                before = plan['before_workload']
                response = self._call('patch', 'deployment/' + before['metadata']['name'], '-n', NAMESPACE,
                                      '--type=json', '-p', json.dumps(self._installation_patch(plan)),
                                      '--dry-run=server', '-o', 'json')
                admitted = json.loads(response.stdout)
                if admitted['metadata']['uid'] != before['metadata']['uid']:
                    raise ValueError('dry-run deployment UID changed')
                plan['after_workload'] = admitted
            self.snapshot = {'name': self.name, 'owner': self.owner, 'namespace': NAMESPACE,
                             'ownership_marker_uid': marker['metadata']['uid'],
                             'cleanup_deadline_epoch': self.deadline + 1800, 'plans': self.plans}
            self._write('watchdog-snapshot.json', self.snapshot)
            for obj in watchdog_objects(self.snapshot, self.image):
                self._create(obj)
            self._wait_watchdog()
            if time.time() >= self.deadline:
                raise TimeoutError('packet deadline reached before app mutation')
            for plan in self.plans:
                if time.time() >= self.deadline:
                    raise TimeoutError('packet deadline reached during app setup')
                before = plan['before_workload']
                patch = self._installation_patch(plan)
                self._call('patch', 'deployment/' + before['metadata']['name'], '-n', NAMESPACE, '--type=json', '-p', json.dumps(patch))
                current = self._get('deployment/' + before['metadata']['name'])
                watchdog.restore_patch(plan, 'workload', current)
                self._call('rollout', 'status', 'deployment/' + before['metadata']['name'], '-n', NAMESPACE, '--timeout=240s', timeout=250)
                candidates = [p for p in self._get('pods')['items'] if p.get('metadata', {}).get('annotations', {}).get(watchdog.OWNER) == self.owner and p['metadata']['name'].startswith(before['metadata']['name'] + '-') and not p['metadata'].get('deletionTimestamp') and p.get('status', {}).get('phase') == 'Running']
                if len(candidates) != 1:
                    raise ValueError('expected exactly one packet pod: ' + before['metadata']['name'])
                self.pods[before['metadata']['name']] = candidates[0]
                self._write(before['metadata']['name'] + '/pod-before.json', candidates[0])
            self.state['started_at_epoch'] = time.time()
            self._write('status.json', self.state)
            return self
        except BaseException as exc:
            self.state['errors'].append('setup:' + str(exc))
            self.stop()
            raise

    def _export(self, name, pod):
        # Every attempt closes capture first and retains its own immutable archive.
        # Failed transfers must not be overwritten by later successful downloads.
        for attempt in range(1, 4):
            record = {'attempt': attempt}
            try:
                self._export_attempt(name, pod, attempt)
                record['valid'] = True
                self._write(name + '/export-attempt-' + str(attempt) + '.json', record)
                return
            except Exception as exc:
                def decoded(value):
                    return value.decode(errors='replace') if isinstance(value, bytes) else value
                record.update(valid=False, error=str(exc), stdout=decoded(getattr(exc, 'stdout', None)),
                              stderr=decoded(getattr(exc, 'stderr', None)))
                self._write(name + '/export-attempt-' + str(attempt) + '.json', record)
        raise RuntimeError('three packet export attempts failed: ' + name)

    def _export_attempt(self, name, pod, attempt):
        dest = self.path / name;dest.mkdir(exist_ok=True)
        pod_name = pod['metadata']['name']
        self._call('exec', '-n', NAMESPACE, pod_name, '-c', 'diag-tcpdump', '--', 'sh', '-c', 'touch /capture/STOP; for n in $(seq 1 15); do test -e /capture/DONE && exit 0; sleep 1; done; exit 1', timeout=25)
        current = self._get('pod/' + pod_name)
        self._write(name + '/pod-after.json', current)
        if current['metadata']['uid'] != pod['metadata']['uid']:
            raise ValueError('capture pod UID changed')
        statuses = {c['name']: c for c in current.get('status', {}).get('containerStatuses', [])}
        if statuses.get('diag-tcpdump', {}).get('restartCount', 0):
            raise ValueError('packet container restarted')
        archive = dest / ('capture-attempt-' + str(attempt) + '.tar')
        with archive.open('xb') as handle:
            self._call('exec', '-n', NAMESPACE, pod_name, '-c', 'diag-tcpdump', '--', 'tar', 'cf', '-', '-C', '/capture', '.', output=handle, timeout=90)
        raw = dest / ('raw-attempt-' + str(attempt))
        with tarfile.open(archive) as tar:
            members = tar.getmembers()
            if sum(m.size for m in members) > MAX_BYTES:
                raise ValueError('packet archive exceeds32Mi')
            for member in members:
                path = Path(member.name)
                if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):
                    raise ValueError('unsafe packet archive')
            raw.mkdir()
            tar.extractall(raw, filter='data')
        required = ('traffic.pcap', 'versions.txt', 'stderr.txt', 'exit-code.txt', 'started-at.txt', 'finished-at.txt', 'DONE')
        if any(not (raw / f).is_file() for f in required):
            raise ValueError('packet capture missing terminal artifacts')
        code = int((raw / 'exit-code.txt').read_text())
        if code not in (0, 124, 130) or (raw / 'traffic.pcap').stat().st_size <= 24:
            raise ValueError('tcpdump failed or captured no packets: ' + str(code))
        finished = datetime.fromisoformat((raw / 'finished-at.txt').read_text().strip().replace('Z', '+00:00')).timestamp()
        coverage = coverage_status((raw / 'stderr.txt').read_text(), finished, self.planned_load_end_epoch)
        if not coverage['valid']:
            self.state['errors'].append({'component': name, 'coverage': coverage})
        self.state['artifacts'][name] = {'exit_code': code, 'coverage': coverage, 'files': {str(p.relative_to(dest)): {'bytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in dest.rglob('*') if p.is_file()}}

    def _restore(self):
        manager = self
        class LocalAPI:
            def call(self, path, method='GET', value=None):
                if method == 'GET':
                    return json.loads(manager._call('get', '--raw', path).stdout)
                parts = path.split('/')
                return json.loads(manager._call('patch', parts[-2] + '/' + parts[-1], '-n', NAMESPACE, '--type=json', '-p', json.dumps(value), '-o', 'json').stdout)
        result = watchdog.run(self.snapshot, LocalAPI(), wait=False)
        if result['restored']:
            for name in TARGETS:
                self._call('rollout', 'status', 'deployment/' + name, '-n', NAMESPACE, '--timeout=240s', timeout=250)
        return result

    def _delete_owned(self):
        # Foreground Job deletion terminates its pod before RBAC/config disappear.
        resources = sorted(set(self.created), key=lambda item: (item[0] != 'job', item[0] == 'configmap'))
        for kind, name in resources:
            response = self._call('get', kind + '/' + name, '-n', NAMESPACE, '--ignore-not-found', '-o', 'json')
            if not response.stdout.strip():
                continue
            obj = json.loads(response.stdout)
            if obj['metadata'].get('labels', {}).get(watchdog.OWNER) != self.owner:
                raise ValueError('auxiliary ownership changed: ' + name)
            if kind == 'configmap' and name == self.name:
                self._write('watchdog-terminal-status.json', obj.get('data', {}))
            self._call('delete', kind + '/' + name, '-n', NAMESPACE, '--cascade=foreground', '--wait=true', '--timeout=90s', timeout=100)

    def stop(self, *, planned_load_end_epoch=None):
        if planned_load_end_epoch is not None:
            self.planned_load_end_epoch = float(planned_load_end_epoch)
            self.state['planned_load_end_epoch'] = self.planned_load_end_epoch
        if self.stopped:
            return copy.deepcopy(self.state)
        try:
            for name, pod in self.pods.items():
                try:
                    self._export(name, pod)
                except Exception as exc:
                    self.state['errors'].append('export ' + name + ':' + str(exc))
        finally:
            try:
                cleanup = self._restore() if self.snapshot else {'restored': True, 'resources': []}
                self.state['cleanup'] = cleanup
                if cleanup.get('restored'):
                    self._delete_owned()
                else:
                    self.state['errors'].append('guarded restoration refused; watchdog retained')
            except Exception as exc:
                self.state['errors'].append('cleanup:' + str(exc))
            self.stopped = True
            self.state['valid'] = bool(len(self.state['artifacts']) == 2 and not self.state['errors'] and self.state.get('cleanup', {}).get('restored'))
            self.state['finished_at_epoch'] = time.time()
            self._write('status.json', self.state)
        return copy.deepcopy(self.state)

    def __enter__(self): return self.start()
    def __exit__(self, *_): self.stop()
