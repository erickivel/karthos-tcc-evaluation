"""Private, read-only Kubernetes API transport for GKE evidence snapshots."""
from __future__ import annotations

import contextlib
import http.client
import json
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
def pod_proxy(namespace, pods, timeout, stderr_path):
    names = sorted(set(pods))
    if not NODE.fullmatch(namespace) or not names or any(not NODE.fullmatch(name) for name in names):
        raise ValueError('exact collector namespace and pod names required')
    paths = '^/api/v1/namespaces/'+re.escape(namespace)+'/pods/('+'|'.join(map(re.escape, names))+')$'
    with _private_proxy(paths, timeout, stderr_path) as path:
        yield path


def pod_request(socket_path, namespace, pod, timeout):
    if not NODE.fullmatch(namespace) or not NODE.fullmatch(pod):
        raise ValueError('exact collector namespace and pod required')
    path = f'/api/v1/namespaces/{namespace}/pods/{pod}'
    with contextlib.closing(UnixHTTPConnection(socket_path, timeout)) as connection:
        connection.request('GET', path, headers={'Accept': 'application/json'})
        with connection.getresponse() as response:
            body = response.read(SUMMARY_BYTES+1)
            if response.status != 200 or len(body) > SUMMARY_BYTES:
                raise ValueError('collector identity HTTP failure or response exceeds byte bound')
    result = json.loads(body)
    if result.get('metadata', {}).get('name') != pod or result.get('metadata', {}).get('namespace') != namespace:
        raise ValueError('collector identity response scope differs')
    return result


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


def _mesh_path(namespace, pod):
    if not NODE.fullmatch(namespace) or not NODE.fullmatch(pod):
        raise ValueError('exact mesh namespace and pod names required')
    return f'/api/v1/namespaces/{namespace}/pods/{pod}:15090/proxy/stats/prometheus'


@contextlib.contextmanager
def mesh_proxy(targets, timeout, stderr_path, *, lifecycle=None):
    paths=sorted({_mesh_path(namespace,pod) for namespace,pod in targets})
    if not paths:raise ValueError('exact mesh targets required')
    pattern='^('+'|'.join(re.escape(path) for path in paths)+')$'
    with _private_proxy(pattern,timeout,stderr_path,lifecycle=lifecycle) as path:
        yield path


def mesh_request(socket_path, namespace, pod, timeout):
    """Same read-only endpoint; one wall-clock budget across every HTTP stage."""
    path=_mesh_path(namespace,pod)
    started=time.monotonic();expires=started+timeout;phase='connect';phase_started=started
    diagnostic={'transport':'private-kubernetes-proxy','timeout_seconds':timeout,'stages':{},
                'upstream_scope':'header wait includes authentication, network, API and Envoy; not individually attributed',
                'requested_at_ns':time.time_ns()}
    def remaining(connection):
        budget=expires-time.monotonic()
        if budget<=0:raise TimeoutError('mesh request deadline exhausted')
        if connection.sock is not None:connection.sock.settimeout(budget)
        return budget
    try:
        with contextlib.closing(UnixHTTPConnection(socket_path,timeout)) as connection:
            remaining(connection);connection.connect()
            diagnostic['stages'][phase]=time.monotonic()-phase_started
            phase,phase_started='request',time.monotonic()
            remaining(connection);connection.request('GET',path,headers={'Accept':'text/plain'})
            diagnostic['stages'][phase]=time.monotonic()-phase_started
            phase,phase_started='headers',time.monotonic()
            remaining(connection)
            with connection.getresponse() as response:
                diagnostic['stages'][phase]=time.monotonic()-phase_started
                phase,phase_started='body',time.monotonic();body=bytearray()
                while True:
                    remaining(connection)
                    chunk=response.read1(min(65536,SUMMARY_BYTES+1-len(body)))
                    if not chunk:break
                    body.extend(chunk)
                    if len(body)>SUMMARY_BYTES:raise ValueError('mesh response exceeds byte bound')
                diagnostic['stages'][phase]=time.monotonic()-phase_started
                status=response.status
    except Exception as exc:
        diagnostic.update(failed_stage=phase,elapsed_seconds=time.monotonic()-started,
                          failed_stage_seconds=time.monotonic()-phase_started,
                          exception_type=type(exc).__name__,received_at_ns=time.time_ns())
        exc.transport_diagnostic=diagnostic
        raise
    diagnostic.update(elapsed_seconds=time.monotonic()-started,http_status=status,received_at_ns=time.time_ns())
    result=subprocess.CompletedProcess(['GET',path],0 if status==200 else status,bytes(body),
                                       b'' if status==200 else f'HTTP {status}'.encode())
    result.transport_diagnostic=diagnostic
    return result
