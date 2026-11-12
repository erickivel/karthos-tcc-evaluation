"""Private, read-only Kubernetes API transport for GKE evidence snapshots."""
from __future__ import annotations

import contextlib
import http.client
from pathlib import Path
import re
import socket
import stat
import subprocess
import tempfile
import time
from urllib.parse import urlencode

NODE = re.compile(r'[a-z0-9][a-z0-9.-]*')
SUMMARY_BYTES = 32 * 1024 * 1024


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout):
        super().__init__('localhost', timeout=timeout)
        self.path = str(path)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


@contextlib.contextmanager
def kubelet_proxy(nodes, timeout, stderr_path, *, lifecycle=None):
    names = sorted(set(nodes))
    if not names or any(not NODE.fullmatch(node) for node in names):
        raise ValueError('exact kubelet node names required')
    paths = r'^/api/v1/nodes/('+'|'.join(re.escape(node) for node in names)+r')/proxy/stats/summary$'
    with _private_proxy(paths, timeout, stderr_path, lifecycle=lifecycle) as path:
        yield path


def _log_path(namespace, pod):
    if not NODE.fullmatch(namespace) or not NODE.fullmatch(pod):
        raise ValueError('exact log namespace and pod names required')
    return f'/api/v1/namespaces/{namespace}/pods/{pod}/log'


@contextlib.contextmanager
def log_proxy(namespaces, timeout, stderr_path, *, lifecycle=None):
    names = sorted(set(namespaces))
    if not names or any(not NODE.fullmatch(ns) for ns in names):
        raise ValueError('exact log namespaces required')
    scope = '|'.join(map(re.escape, names))
    paths = f'^/api/v1/namespaces/({scope})/pods/{NODE.pattern}/log$'
    with _private_proxy(paths, timeout, stderr_path, lifecycle=lifecycle) as path:
        yield path


@contextlib.contextmanager
def _private_proxy(paths, timeout, stderr_path, *, lifecycle=None):
    if lifecycle is not None:
        lifecycle.update(started=False, stopped=True, pid=None, returncode=None)
    with tempfile.TemporaryDirectory(prefix='kube-read-', dir='/tmp') as folder, Path(stderr_path).open('wb') as stderr:
        path = Path(folder)/'api.sock'
        command = ['kubectl', 'proxy', '--unix-socket='+str(path), '--accept-paths='+paths,
                   '--reject-methods=^(POST|PUT|PATCH|DELETE|CONNECT|TRACE|OPTIONS|HEAD)$']
        with subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=stderr) as process:
            if lifecycle is not None:
                lifecycle.update(started=True, stopped=False, pid=process.pid)
            deadline = time.monotonic()+timeout
            try:
                while not path.exists():
                    if process.poll() is not None:
                        raise RuntimeError('private Kubernetes proxy exited before binding')
                    if time.monotonic() >= deadline:
                        raise TimeoutError('private Kubernetes proxy did not bind')
                    time.sleep(.02)
                if not stat.S_ISSOCK(path.stat().st_mode):
                    raise RuntimeError('private Kubernetes endpoint is not a Unix socket')
                yield path
            finally:
                try:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=2)
                finally:
                    if lifecycle is not None:
                        code = process.poll()
                        lifecycle.update(stopped=code is not None, returncode=code)


def kubelet_request(socket_path, node, timeout):
    if not NODE.fullmatch(node):
        raise ValueError('invalid kubelet node name')
    path = f'/api/v1/nodes/{node}/proxy/stats/summary'
    started = time.monotonic()
    diagnostic = {'transport': 'private-kubernetes-proxy', 'timeout_seconds': timeout,
                  'stages': {}}
    phase = 'connect'
    phase_started = started
    try:
        with contextlib.closing(UnixHTTPConnection(socket_path, timeout)) as connection:
            connection.connect()
            diagnostic['stages'][phase] = time.monotonic() - phase_started
            phase, phase_started = 'request', time.monotonic()
            connection.request('GET', path, headers={'Accept': 'application/json'})
            diagnostic['stages'][phase] = time.monotonic() - phase_started
            phase, phase_started = 'headers', time.monotonic()
            with connection.getresponse() as response:
                diagnostic['stages'][phase] = time.monotonic() - phase_started
                phase, phase_started = 'body', time.monotonic()
                body = response.read(SUMMARY_BYTES+1)
                diagnostic['stages'][phase] = time.monotonic() - phase_started
                status = response.status
    except Exception as exc:
        diagnostic.update(failed_stage=phase, elapsed_seconds=time.monotonic()-started,
                          failed_stage_seconds=time.monotonic()-phase_started,
                          exception_type=type(exc).__name__)
        # Preserve the original exception type/message and transport timeout.
        # Structured metadata distinguishes local connect/write from API waits.
        exc.transport_diagnostic = diagnostic
        raise
    diagnostic.update(elapsed_seconds=time.monotonic()-started, http_status=status)
    if len(body) > SUMMARY_BYTES:
        exc = ValueError('kubelet response exceeds byte bound')
        exc.transport_diagnostic = diagnostic
        raise exc
    result = subprocess.CompletedProcess(['GET', path], 0 if status == 200 else status, body,
                                         b'' if status == 200 else f'HTTP {status}'.encode())
    result.transport_diagnostic = diagnostic
    return result


def log_request(socket_path, namespace, pod, container, cursor, timeout, *, max_bytes):
    if not NODE.fullmatch(container):
        raise ValueError('exact log container required')
    path = _log_path(namespace, pod) + '?' + urlencode(
        {'container': container, 'timestamps': 'true', 'sinceTime': cursor})
    with contextlib.closing(UnixHTTPConnection(socket_path, timeout)) as connection:
        connection.request('GET', path)
        with connection.getresponse() as response:
            body = response.read(max_bytes + 1)
            status = response.status
    if len(body) > max_bytes:
        raise ValueError('log snapshot exceeds byte bound')
    if status != 200:
        return subprocess.CompletedProcess(['GET', path], status, b'', body)
    return subprocess.CompletedProcess(['GET', path], 0, body, b'')
