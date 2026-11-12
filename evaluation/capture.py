"""Continuous evidence collection; each stream keeps its own unmodified file."""
from __future__ import annotations

import json
import contextlib
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from pathlib import Path

from . import runner as r
from .protocol import KARTHOS_NAMESPACE
from .log_capture import PollingLogCapture


def stream_targets(pods: dict, *, include_terminating=True) -> list[tuple[str, str, str]]:
    result = []
    for pod in pods.get('items', []):
        if pod.get('status', {}).get('phase') != 'Running':
            continue
        meta, spec = pod['metadata'], pod.get('spec', {})
        if not include_terminating and meta.get('deletionTimestamp'):
            continue
        containers = spec.get('containers', []) + [c for c in spec.get('initContainers', [])
                                                   if c.get('restartPolicy') == 'Always']
        result += [(meta['namespace'], meta['name'], c['name']) for c in containers]
    return result


class ContinuousCapture:
    log_capture_options = {}

    def __init__(self, cell, path: Path):
        self.cell, self.path = cell, path
        self.stop_event = threading.Event()
        self.log_captures = []
        self.stopped = False
        self.thread = None
        self.started_at = None
        self.mesh_pods = []
        self.collector_pods = []
        self.collector_forwards = []
        self.stack = contextlib.ExitStack()

    def start(self):
        try:
            return self._start()
        except BaseException:
            self.stop()
            raise

    def _collector_target(self,namespace,pod,container):
        return (namespace==KARTHOS_NAMESPACE and ('agent' in pod or 'gateway' in pod)
                and container!='istio-proxy' and (not hasattr(self,'collector_container_names')
                or container in self.collector_container_names))

    def _start(self):
        self.path.mkdir(parents=True, exist_ok=True)
        self.started_at = r.utc()
        namespaces = (self.cell.namespace, KARTHOS_NAMESPACE)
        options = dict(self.log_capture_options)
        snapshot = self._log_snapshot(namespaces)
        if snapshot is not None:
            options['snapshot'] = snapshot
        for ns in namespaces:
            pods = r.kube_json('get', 'pods', '-n', ns)
            r.write(self.path / f'pods-before-{ns}.json', pods)
            for namespace, pod, container in stream_targets(pods, include_terminating=False):
                if container == 'istio-proxy':
                    self.mesh_pods.append((namespace, pod))
                if self._collector_target(ns,pod,container):
                    self.collector_pods.append((namespace,pod))
                basename = f'{namespace}__{pod}__{container}'
                log_path = self.path / 'logs' / (basename + '.log')
                capture = PollingLogCapture(namespace, pod, container, log_path, self.started_at, **options)
                # Register first so setup failures still stop already started capture.
                self.log_captures.append(capture)
                capture.start()
        self.node = r.kube_json('get', 'nodes')['items'][0]['metadata']['name']
        # Collector self metrics bind to loopback by default. Port-forward
        # reaches that endpoint without changing Karthos collection config.
        for index, (ns, pod) in enumerate(self.collector_pods):
            try:
                base = self.stack.enter_context(r.forward('pod/' + pod, ns, 18880 + index, 8888))
                self.collector_forwards.append((ns, pod, base))
            except Exception as exc:
                r.write(self.path / f'collector-forward-{pod}.error', str(exc))
        self._before_sampling()
        self.thread = threading.Thread(target=self._sample, daemon=True)
        self.thread.start()
        return self

    def _log_snapshot(self, namespaces):
        return None

    def _before_sampling(self):
        pass

    def _sample(self):
        with (self.path / 'kubelet.ndjson').open('w') as summary_file, \
             (self.path / 'istio-samples.ndjson').open('w') as mesh_file, \
             (self.path / 'collector-metrics.ndjson').open('w') as collector_file:
            sequence = 0
            while not self.stop_event.is_set():
                try:
                    stamp = r.utc()
                    result = r.kube('get', '--raw', f'/api/v1/nodes/{self.node}/proxy/stats/summary',
                                    check=False, timeout=20, quiet=True)
                    row = {'captured_at': stamp}
                    if result.returncode == 0:
                        row['summary'] = json.loads(result.stdout)
                    else:
                        row['error'] = result.stderr
                    summary_file.write(json.dumps(row) + '\n')
                    summary_file.flush()
                    if sequence % 3 == 0:
                        # Two app endpoints are sufficient for the selected edge;
                        # the final inventory retains all mesh sidecars.
                        selected = [x for x in self.mesh_pods if 'checkout' in x[1] or 'frontend' in x[1]]
                        for ns, pod in selected:
                            self._proxy_sample(mesh_file, ns, pod, 15090, '/stats/prometheus')
                        for ns, pod, base in self.collector_forwards:
                            try:
                                with r.urllib.request.urlopen(base + '/metrics', timeout=3) as response:
                                    text = response.read().decode()
                                collector_file.write(json.dumps({'captured_at': r.utc(), 'namespace': ns,
                                                                  'pod': pod, 'text': text, 'error': None})+'\n')
                            except Exception as exc:
                                collector_file.write(json.dumps({'captured_at': r.utc(), 'namespace': ns,
                                                                  'pod': pod, 'text': '', 'error': str(exc)})+'\n')
                            collector_file.flush()
                except Exception as exc:
                    with (self.path / 'capture-errors.ndjson').open('a') as errors:
                        errors.write(json.dumps({'captured_at': r.utc(), 'error': str(exc)}) + '\n')
                sequence += 1
                self.stop_event.wait(5)

    def _proxy_sample(self, handle, ns, pod, port, suffix):
        result = r.kube('get', '--raw', f'/api/v1/namespaces/{ns}/pods/{pod}:{port}/proxy{suffix}',
                        check=False, timeout=10, quiet=True)
        handle.write(json.dumps({'captured_at': r.utc(), 'namespace': ns, 'pod': pod,
                                  'text': result.stdout if result.returncode == 0 else '',
                                  'error': result.stderr if result.returncode else None}) + '\n')
        handle.flush()

    def stop(self):
        if self.stopped:
            return
        self.stop_event.set()
        # Stop all log snapshots concurrently: total final-drain allowance is
        # bounded by one in-flight poll plus one final poll, not pod count.
        with ThreadPoolExecutor(max_workers=max(1, len(self.log_captures))) as executor:
            pending = [executor.submit(capture.stop) for capture in self.log_captures]
            if self.thread:
                self.thread.join(timeout=45)
            for result in pending:
                result.result()
        states = [capture.status() for capture in self.log_captures]
        self.stack.close()
        self.path.mkdir(parents=True, exist_ok=True)
        r.write(self.path / 'capture-status.json', {'started_at': self.started_at, 'finished_at': r.utc(),
                                                   'sampler_stopped': not (self.thread and self.thread.is_alive()),
                                                   'streams': states})
        self.stopped = True
