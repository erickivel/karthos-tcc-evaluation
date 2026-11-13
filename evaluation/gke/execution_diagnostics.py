"""Bounded supplementary execution evidence; never an accuracy oracle or run gate."""
from __future__ import annotations

from collections import deque
from contextlib import suppress
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
import time

from .export import redact_text


class ExecutionDiagnostics:
    """Collect lifecycle, logs and controller observations for explicit namespaces.

    Collection failures are recorded, never raised to replace a workload error.
    Call stop before sealing/exporting path. A false stopped flag means the
    caller must not seal a directory still being written by the worker.
    """
    def __init__(self, ctx, path, namespaces, *, poll_seconds=5,
                 instance_limit_bytes=2*1024**2, total_limit_bytes=256*1024**2):
        self.ctx, self.path = ctx, Path(path)
        self.namespaces = tuple(sorted(set(namespaces)))
        self.poll_seconds = poll_seconds
        self.instance_limit = instance_limit_bytes
        self.total_limit = total_limit_bytes
        self.started_at = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        self.stop_event = threading.Event()
        self.thread = None
        self.states = {}
        self.queue = deque()
        self.used_bytes = 0
        self.errors = 0
        self.saturated = False
        self.start_error = None
        self._stopped = False
        self.log_cursors = {}

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.stop()
        return False

    def _write(self, name, value, *, append=False):
        data = value if isinstance(value, bytes) else (json.dumps(value, sort_keys=True)+'\n').encode()
        target = self.path/name
        old = target.stat().st_size if target.exists() and not append else 0
        if self.used_bytes-old+len(data)>self.total_limit:
            self.saturated = True
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('ab' if append else 'wb') as stream:
            stream.write(data)
        self.used_bytes += len(data)-old
        return True

    def _error(self, operation, exc, **identity):
        self.errors += 1
        with suppress(Exception):
            self._write('errors.ndjson', {'observed_at':time.time(), 'operation':operation,
                        'type':type(exc).__name__, 'error':redact_text(str(exc))[:2000], **identity}, append=True)

    def start(self):
        if self.thread is not None or self._stopped:
            return self
        try:
            self.path.mkdir(parents=True, exist_ok=False)
            self.thread = threading.Thread(target=self._loop, name='execution-diagnostics', daemon=True)
            self.thread.start()
        except Exception as exc:
            self.start_error = redact_text(str(exc))[:2000]
            self.errors += 1
        return self

    def _json(self, *args):
        response = self.ctx.kube(*args, '-o', 'json', timeout=5, check=False, cleanup=True)
        if response.returncode:
            raise RuntimeError(str(response.stderr)[-2000:])
        return json.loads(response.stdout)

    def _events(self, boundary):
        for namespace in self.namespaces:
            if self.saturated:
                break
            try:
                payload = self._json('get', 'events', '-n', namespace)
                self._write('events.ndjson', {'observed_at':time.time(), 'boundary':boundary,
                            'namespace':namespace, 'items':payload.get('items', [])}, append=True)
            except Exception as exc:
                self._error('events', exc, namespace=namespace)

    @staticmethod
    def _statuses(pod):
        status = pod.get('status', {})
        for field in ('containerStatuses', 'initContainerStatuses', 'ephemeralContainerStatuses'):
            for container in status.get(field, []):
                yield field, container

    def _inventory(self):
        for namespace in self.namespaces:
            if self.stop_event.is_set() or self.saturated:
                return
            try:
                payload = self._json('get', 'pods', '-n', namespace)
                seen = set()
                for pod in payload.get('items', []):
                    meta, spec, status = pod.get('metadata', {}), pod.get('spec', {}), pod.get('status', {})
                    uid = meta.get('uid')
                    seen.add(uid)
                    containers = []
                    for kind, container in self._statuses(pod):
                        record = {k:container.get(k) for k in ('name','containerID','restartCount','ready','started','state','lastState')}
                        record['status_kind'] = kind
                        containers.append(record)
                        self._observe_instance(namespace, meta.get('name'), uid, record)
                    self._write('lifecycle.ndjson', {'observed_at':time.time(), 'namespace':namespace,
                        'name':meta.get('name'), 'uid':uid, 'deletion_timestamp':meta.get('deletionTimestamp'),
                        'node':spec.get('nodeName'), 'phase':status.get('phase'), 'reason':status.get('reason'),
                        'message':status.get('message'), 'conditions':status.get('conditions', []),
                        'containers':containers, 'resources':[{ 'name':c.get('name'), 'resources':c.get('resources', {}),
                        'restartPolicy':c.get('restartPolicy')} for field in ('containers','initContainers')
                        for c in spec.get(field, [])]}, append=True)
                for key, item in self.states.items():
                    if item['namespace']==namespace and item['uid'] not in seen and not item.get('missing'):
                        item['missing'] = True
                        self._write('lifecycle.ndjson', {'observed_at':time.time(), 'event':'pod-no-longer-observed',
                                    'namespace':namespace, 'pod':item['pod'], 'uid':item['uid'],
                                    'instance':key, 'previous_log_availability':'unknown; pod absent'}, append=True)
            except Exception as exc:
                self._error('inventory', exc, namespace=namespace)

    def _observe_instance(self, namespace, pod, uid, container):
        identity = [namespace, uid, container['name'], container['containerID'], container['restartCount']]
        key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        if key in self.states:
            return
        for prior in self.states.values():
            if prior['uid']==uid and prior['container']==container['name']:
                prior['retired'] = True
        item = dict(namespace=namespace, pod=pod, uid=uid, container=container['name'],
                    container_id=container['containerID'], restart_count=container['restartCount'], instance=key)
        self.states[key] = item
        self.queue.append(item)
        last = (container.get('lastState') or {}).get('terminated')
        if last:
            previous_item = dict(item, terminated_instance=last)
            self._logs(previous_item, previous=True)

    def _logs(self, item, *, previous=False):
        if self.saturated or self.stop_event.is_set():
            return
        terminated = item.get('terminated_instance', {})
        container_id = terminated.get('containerID') if previous else item['container_id']
        identity = {'containerID':container_id} if container_id else {'restart_count':item['restart_count']-int(previous)}
        log_key = hashlib.sha256(json.dumps([item['uid'], item['container'], identity], sort_keys=True).encode()).hexdigest()
        history = self.log_cursors.setdefault(log_key, {'bytes':0, 'chunks':0, 'since':self.started_at})
        remaining = self.instance_limit-history['bytes']
        if remaining <= 0:
            if not history.get('budget_reported'):
                self._write('log-index.ndjson', {**item, 'path':None, 'observed_at':time.time(),
                    'coverage_gap':'instance byte budget exhausted'}, append=True)
                history['budget_reported'] = True
            return
        requested_since = history['since']
        args = ['logs', item['pod'], '-n', item['namespace'], '-c', item['container'],
                '--timestamps=true', '--limit-bytes='+str(remaining)]
        if previous:
            args.append('--previous')
        else:
            args.append('--since-time='+requested_since)
        requested = time.time()
        try:
            response = self.ctx.kube(*args, timeout=5, check=False, cleanup=True)
            attributed = False
            try:
                latest = self._json('get', 'pod', item['pod'], '-n', item['namespace'])
                matching = next((c for _,c in self._statuses(latest) if c.get('name')==item['container']), {})
                attributed = (latest.get('metadata', {}).get('uid')==item['uid']
                              and matching.get('restartCount')==item['restart_count']
                              and matching.get('containerID')==item['container_id'])
            except Exception as exc:
                self._error('log-identity-recheck', exc, **item)
            scope = 'previous' if previous else 'current'
            # Keep raced logs as explicitly unattributed diagnostics, never an oracle.
            history['chunks'] += 1
            name = 'logs/'+log_key+'/'+str(history['chunks']).zfill(6)+'.log'
            raw = response.stdout if isinstance(response.stdout, bytes) else response.stdout.encode()
            output = raw[:remaining]
            saved = self._write(name, output)
            partial = bool(output and not output.endswith(b'\n'))
            if saved:
                history['bytes'] += len(output)
                # The inclusive timestamp cursor deliberately preserves boundary
                # overlap. Do not deduplicate equal timestamps or identical lines:
                # distinct application events can have identical contents.
                if response.returncode == 0 and attributed and not previous:
                    for line in output.splitlines(keepends=True):
                        if not line.endswith(b'\n'):
                            continue
                        stamp = line.split(b' ',1)[0].decode('ascii', errors='replace')
                        try:
                            parsed = datetime.fromisoformat(stamp.replace('Z','+00:00'))
                            old = datetime.fromisoformat(history['since'].replace('Z','+00:00'))
                            if parsed >= old:
                                history['since'] = stamp
                        except (ValueError, TypeError):
                            pass
            self._write('log-index.ndjson', {**item, 'scope':scope, 'requested_at':requested,
                'received_at':time.time(), 'returncode':response.returncode, 'attributed':attributed,
                'path':name if saved else None, 'bytes':len(output), 'truncation_possible':len(raw)>=remaining,
                'requested_since_time':None if previous else requested_since, 'overlap_possible':not previous,
                'partial_last_line':partial, 'sha256':hashlib.sha256(output).hexdigest(),
                'coverage_gap':('request byte bound reached' if len(raw)>=remaining else None),
                'previous_scope':'last terminated instance only' if previous else None,
                'stderr':redact_text(str(response.stderr))[-2000:]}, append=True)
        except Exception as exc:
            self._error('previous-logs' if previous else 'current-logs', exc, **item)

    def _controller(self):
        row = {'observed_at':time.time(), 'pid':os.getpid()}
        try:
            import resource
            usage = resource.getrusage(resource.RUSAGE_SELF)
            row.update(cpu_user_seconds=usage.ru_utime, cpu_system_seconds=usage.ru_stime,
                       peak_rss=usage.ru_maxrss, peak_rss_units='KiB on Linux; bytes on macOS', load=os.getloadavg())
            import shutil
            row['disk_free_bytes'] = shutil.disk_usage(self.path).free
            for name, file in (('process_status','/proc/self/status'), ('process_io','/proc/self/io'),
                               ('memory','/proc/meminfo')):
                if Path(file).exists():
                    row[name] = Path(file).read_text()[:16384]
            task = Path('/proc/self/task')
            if task.exists():
                row['child_pids'] = sorted({int(pid) for path in task.glob('*/children') for pid in path.read_text().split()})
            self._write('controller.ndjson', row, append=True)
        except Exception as exc:
            self._error('controller', exc)

    def _loop(self):
        try:
            self._events('start')
            while not self.stop_event.is_set() and not self.saturated:
                began = time.monotonic()
                self._inventory()
                self._controller()
                # Bound diagnostics API pressure; lifecycle changes have priority.
                for _ in range(min(2, len(self.queue))):
                    item = self.queue.popleft()
                    if not item.get('missing') and not item.get('retired'):
                        self._logs(item)
                        self.queue.append(item)
                self._write('cadence.ndjson', {'observed_at':time.time(), 'iteration_seconds':time.monotonic()-began,
                            'target_seconds':self.poll_seconds}, append=True)
                self.stop_event.wait(max(0, self.poll_seconds-(time.monotonic()-began)))
        except Exception as exc:
            self._error('worker', exc)
        finally:
            self._events('stop')

    def stop(self):
        self.stop_event.set()
        if self.thread is not None and self.thread.ident is not None:
            with suppress(Exception):
                self.thread.join(timeout=25)
        stopped = self.thread is None or not self.thread.is_alive()
        summary = {'supplementary':True, 'stopped':stopped, 'errors':self.errors,
                   'start_error':self.start_error, 'byte_limit_reached':self.saturated,
                   'retained_bytes':self.used_bytes, 'total_limit_bytes':self.total_limit,
                   'instance_log_limit_bytes':self.instance_limit,
                   'poll_seconds':self.poll_seconds, 'instances':len(self.states),
                   'coverage':'best effort; immutable incremental chunks with explicit inclusive-cursor overlap and byte-budget gaps',
                   'instance_budgets_exhausted':sum(bool(v.get('budget_reported')) for v in self.log_cursors.values())}
        if stopped:
            self._stopped = True
            with suppress(Exception):
                self.path.mkdir(parents=True, exist_ok=True)
                self.ctx.write(self.path/'diagnostics-status.json', summary)
        return summary
