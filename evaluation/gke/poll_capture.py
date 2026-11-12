"""Persistent API polls and equivalent product-compiled storage diagnostics."""
from __future__ import annotations
import json
import time
import urllib.parse
import urllib.request
import urllib.error
import tempfile
import threading
from pathlib import Path
from .models import write

MAX_POLL_BYTES = 16 * 1024 * 1024


_COMPILERS = {}
_COMPILER_LOCK = threading.Lock()


def compiler_binary(root, deadline):
    """Compile once per product checkout; never run the Go toolchain per poll."""
    from .. import runner as r
    key = (str(Path(root).resolve()), str(r.REPO))
    remaining = deadline-time.monotonic()
    if remaining <= 0 or not _COMPILER_LOCK.acquire(timeout=max(0, remaining)):
        raise TimeoutError('storage compiler lock deadline reached')
    try:
        if key not in _COMPILERS:
            directory = tempfile.TemporaryDirectory(prefix='karthos-query-compiler-')
            binary = str(Path(directory.name)/'render-query')
            try:
                r.cmd(['go', 'build', '-o', binary, str(Path(root)/'evaluation/helpers/render_query.go')],
                      cwd=r.REPO, timeout=max(.001, deadline-time.monotonic()))
            except BaseException:
                directory.cleanup()
                raise
            _COMPILERS[key] = (directory, binary)
        return _COMPILERS[key][1]
    finally:
        _COMPILER_LOCK.release()


def prewarm(ctx, path, deadline):
    """Fail before workload if the pinned product compiler cannot be prepared.

    Call during existing setup allowance, before starting workload clocks. No
    dependency download is attempted here beyond normal Go build behavior.
    """
    import hashlib
    from .. import runner as r
    started = time.monotonic()
    receipt = {'state':'failed', 'product_checkout':str(r.REPO),
               'helper':str(Path(ctx.root)/'evaluation/helpers/render_query.go')}
    try:
        binary = compiler_binary(ctx.root, deadline)
        receipt.update(state='ready', binary_sha256=hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
                       helper_sha256=hashlib.sha256(Path(receipt['helper']).read_bytes()).hexdigest())
    except Exception as exc:
        receipt['error'] = str(exc)
        raise
    finally:
        receipt['elapsed_seconds'] = time.monotonic()-started
        write(path, receipt)
    return receipt


def bounded_json(url, deadline, maximum):
    """Limit socket time and expanded response bytes before JSON decoding."""
    from .. import runner as r
    remaining = min(deadline-time.monotonic(), r.http_timeout())
    if remaining <= 0:
        raise TimeoutError('poll transport deadline reached')
    try:
        response = urllib.request.urlopen(url, timeout=remaining)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        chunks, size = [], 0
        read = getattr(response, 'read1', response.read)
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError('poll transport deadline reached')
            chunk = read(min(65536, maximum-size+1))
            if not chunk:
                break
            size += len(chunk)
            if size > maximum:
                raise ValueError('poll response byte bound exceeded')
            chunks.append(chunk)
    body = b''.join(chunks)
    if isinstance(response, urllib.error.HTTPError):
        raise RuntimeError('HTTP '+str(response.code)+': '+body.decode('utf-8', errors='replace'))
    return json.loads(body)


def compiled_storage(params, root, fetch, deadline, compiled=None):
    """Render with production Go code, then execute the exact evaluation grid.

    fetch receives (route, request); callers retain their own transport and
    byte/cancellation bounds. A compiled fixed target can be reused until it changes.
    """
    from .. import runner as r
    from ..semantics_pilot import vm_query_request
    from ..scoring import stamp
    if compiled is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('storage compiler deadline reached')
        binary = compiler_binary(root, deadline)
        compiled = json.loads(r.cmd([binary], cwd=r.REPO, input_text=json.dumps(params),
                                    timeout=max(.001, deadline-time.monotonic())).stdout)
    request = vm_query_request(compiled['expression'], start=compiled['from'], end=compiled['to'],
                               step=str(compiled['step_seconds']))
    document = {'request':request, 'response':fetch('/api/v1/query_range', request)}
    points = compiled['evaluation_points']
    if len(points) > 1 and abs(stamp(points[-1])-stamp(points[-2])-compiled['step_seconds']) > 1e-6:
        final = vm_query_request(compiled['expression'], time=points[-1])
        document['final'] = {'request':final, 'response':fetch('/api/v1/query', final)}
    return compiled, document


def capture(ctx, path, base, params, deadline):
    """Return (payload, receipt); keep errors/pages even when a query cannot pass.

    The caller supplies the existing monotonic study deadline. Storage uses the
    exact API request and production compiler; it never substitutes a raw selector.
    """
    from .. import runner as r
    path = Path(path)
    record = {'request': dict(params), 'started_at': r.utc(), 'state': 'incomplete',
              'byte_limit': MAX_POLL_BYTES}
    used = 0

    def save(name, value):
        nonlocal used
        size = len(json.dumps(value, sort_keys=True).encode())
        if used + size > MAX_POLL_BYTES:
            raise ValueError('poll evidence byte bound exceeded')
        write(path / name, value)
        used += size

    def remaining():
        seconds = min(deadline - time.monotonic(), r.http_timeout())
        if seconds <= 0:
            raise TimeoutError('poll capture deadline reached')
        return seconds

    payload = None
    try:
        items, token, seen = [], None, set()
        for index in range(1000):
            remaining()
            request = dict(params, page_size='1000')
            if token:
                request['next_token'] = token
            try:
                page = bounded_json(base + '/api/v1/metrics?' + urllib.parse.urlencode(request), deadline, MAX_POLL_BYTES-used)
            except Exception as exc:
                save(f'page-{index:04d}.json', {'request': request, 'error': str(exc), 'received_at': r.utc()})
                raise
            save(f'page-{index:04d}.json', {'request': request, 'response': page, 'received_at': r.utc()})
            items.extend(page.get('data', {}).get('samples', []))
            token = page.get('next_token')
            if not token:
                payload = {'data': {'samples': items, 'query': page.get('data', {}).get('query'), 'pages': index + 1}}
                save('api.json', payload)
                record['api_completed_monotonic'] = time.monotonic()
                record['response_query'] = payload['data']['query']
                record['state'] = 'captured'
                break
            if token in seen:
                raise ValueError('repeated API pagination token')
            seen.add(token)
        else:
            raise ValueError('API pagination bound exceeded')
    except Exception as exc:
        record['api_error'] = str(exc)
    # Independent storage capture remains useful after empty/error API responses.
    try:
        services = json.loads(r.kube('get', 'services', '-n', r.KARTHOS_NAMESPACE, '-o', 'json', timeout=remaining()).stdout).get('items', [])
        vm = next(s['metadata']['name'] for s in services if 'victoria' in s['metadata']['name'])
        with r.forward('service/' + vm, r.KARTHOS_NAMESPACE, 28984, 8428) as vm_base:
            def fetch(route, request):
                remaining()
                response = bounded_json(vm_base + route + '?' + urllib.parse.urlencode(request), deadline, MAX_POLL_BYTES-used)
                save('storage-response-'+('range' if route.endswith('query_range') else 'final')+'.json',
                     {'request':request, 'response':response})
                return response
            compiled, document = compiled_storage(params, ctx.root, fetch, deadline)
            save('compiled.json', compiled)
            save('storage.json', document)
            if payload is not None:
                from .parity import compare
                record['parity'] = compare(payload, document, params['source'], metric=params['metric_name'], unit=compiled.get('unit'))
    except Exception as exc:
        record['storage_error'] = str(exc)
    record.update(completed_at=r.utc(), stored_bytes=used)
    write(path / 'receipt.json', record)
    return payload, record
