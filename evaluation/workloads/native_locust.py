"""Observe upstream Locust HTTP journeys without replacing their tasks or pacing.

The entrypoint installs a request wrapper plus native Locust event listeners,
then invokes Locust's original CLI in the image's original working directory.
Only correlation headers/context are added. Payloads, response handling,
redirect behavior, retries and task scheduling remain owned by upstream code.
"""
from __future__ import annotations

import functools
import hashlib
import itertools
import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit, urljoin


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def trace_id_from_headers(headers):
    values=[value for key,value in (headers or {}).items() if key.lower()=='traceparent']
    if len(values)!=1 or not isinstance(values[0],str):return None
    match=re.fullmatch(r'00-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}',values[0])
    return match[1] if match and int(match[1],16) and int(match[2],16) else None


def request_boundary(url, base_url):
    """Only reviewed destinations are eligible for independent boundary checks."""
    try:
        base=urlsplit(base_url or '')
        target=urlsplit(urljoin(base_url or '',url))
        def origin(value):
            return value.scheme,value.hostname,value.port or (443 if value.scheme=='https' else 80)
        if target.scheme not in ('http','https') or not target.hostname:return 'unknown'
        if origin(target)==origin(base):return 'frontend'
        agent_host=os.environ.get('AGENT_ENDPOINT','agent')
        agent_port=int(os.environ.get('AGENT_PORT','8010'))
        if origin(target)==('http',agent_host,agent_port) and target.path=='/prompt':return 'agent'
    except (ValueError,TypeError):pass
    return 'unknown'


def route_name(method, url):
    """A bounded evaluation label; never retain arbitrary URLs or query values."""
    path = urlsplit(url).path.rstrip('/') or '/'
    exact = {'/': 'home', '/cart': 'cart', '/cart/checkout': 'checkout',
             '/api/cart': 'cart', '/api/checkout': 'checkout',
             '/api/products': 'products', '/api/recommendations': 'recommendations',
             '/api/currency': 'currency', '/setCurrency': 'currency',
             '/emptycart': 'cart_empty', '/api/shipping': 'shipping', '/prompt': 'agent_prompt'}
    if path in exact:
        label = exact[path]
        if label == 'cart':
            return 'cart_add' if method.upper() == 'POST' else 'cart_view'
        return label
    if path.startswith(('/product/', '/api/products/')):
        return 'product'
    return 'other'


def install(events, session_class, run_id, duration_seconds):
    if not run_id or duration_seconds <= 0:
        raise ValueError('run ID and positive duration are required')
    session_classes = session_class if isinstance(session_class, tuple) else (session_class,)
    if not session_classes or len(set(session_classes)) != len(session_classes):
        raise ValueError('distinct session classes are required')
    if any(getattr(cls.request, '_evaluation_native_adapter', False) for cls in session_classes):
        raise ValueError('native observer is already installed')
    counter = itertools.count()
    window_start = None

    def emit(kind, row):
        print('EVAL_' + kind + ' ' + json.dumps(row, separators=(',', ':')), flush=True)

    def window(environment=None, ended=False, **kwargs):
        nonlocal window_start
        if not ended:
            window_start = timestamp()
        emit('WINDOW', dict(run_id=run_id, generator='locust', mode='native',
             started_at=window_start, ended_at=timestamp() if ended else None,
             intended_duration_seconds=duration_seconds,
             target_iterations_per_second=None, expected_iterations=None))

    events.test_start.add_listener(window)
    events.test_stop.add_listener(lambda **kwargs: window(ended=True, **kwargs))

    def terminal(record, response=None, exception=None, response_time=None, native_reported=False, **kwargs):
        if record['terminal_written']:
            return
        record['terminal_written'] = True
        status = getattr(response, 'status_code', 0) or 0
        prepared=getattr(response,'request',None)
        headers=getattr(prepared,'headers',None)
        actual_trace=trace_id_from_headers(headers)
        actual_url=getattr(prepared,'url',None) or record['url']
        emit('REQUEST', dict(record['attempt'], completed_at=timestamp(),
             trace_id=actual_trace,trace_context_evidence='response.request.headers' if actual_trace else 'unavailable',
             expected_boundary=request_boundary(actual_url,record['base_url']),
             duration_ms=response_time if response_time is not None else (time.perf_counter()-record['tick'])*1000,
             status=status, native_reported=native_reported, native_failure=bool(native_reported and exception is not None),
             exception_type=type(exception).__name__ if exception is not None else None, exception=str(exception) if exception is not None else None,
             expected_status=None, outcome_ok=None, attempted=True,
             completed=status > 0, outcome_classification='unclassified-native'))

    def request_finished(context=None, **kwargs):
        record = (context or {}).get('_evaluation_native')
        if record:
            terminal(record, native_reported=True, **kwargs)

    events.request.add_listener(request_finished)
    def wrap(original):

        @functools.wraps(original)
        def request(session, method, url, *args, **kwargs):
            request_id = f'{run_id}:native:{next(counter)}'
            trace_id = hashlib.sha256(request_id.encode()).hexdigest()[:32]
            parent_id = hashlib.sha256((request_id+':client').encode()).hexdigest()[:16]
            headers = dict(kwargs.get('headers') or {})
            headers['X-Evaluation-Run'] = run_id
            headers['X-Evaluation-Request-ID'] = request_id
            if not any(key.lower() == 'traceparent' for key in headers):
                headers['traceparent'] = f'00-{trace_id}-{parent_id}-01'
            attempt = dict(run_id=run_id, generator='locust', mode='native',
                           request_id=request_id, journey_id=None,
                           route=route_name(method,url), method=method.upper(),
                           started_at=timestamp(),trace_id=None,requested_trace_id=trace_id_from_headers(headers),
                           trace_context_evidence='not-yet-sent',ledger_contract_version='native-v2-final-request',
                           expected_boundary=request_boundary(url,getattr(session,'base_url',None) or os.environ.get('LOCUST_HOST','')))
            record = dict(attempt=attempt, tick=time.perf_counter(), terminal_written=False,
                          url=url,base_url=getattr(session,'base_url',None) or os.environ.get('LOCUST_HOST',''))
            context = dict(kwargs.get('context') or {})
            if '_evaluation_native' in context:
                raise ValueError('native evaluation context collision')
            context['_evaluation_native'] = record
            kwargs.update(headers=headers, context=context)
            emit('ATTEMPT', attempt)
            try:
                # catch_response managers emit their event on __exit__, after this
                # call returns. Do not synthesize an early terminal in finally.
                return original(session, method, url, *args, **kwargs)
            except BaseException as exc:
                terminal(record, exception=exc)
                raise

        request._evaluation_native_adapter = True
        return request

    for cls in session_classes:
        cls.request = wrap(cls.request)


def main():
    from locust import events
    from locust.clients import HttpSession
    from locust.contrib.fasthttp import FastHttpSession
    from locust.main import main as locust_main
    from locust_reports import install as install_reports
    install_reports(events)
    install(events, (HttpSession, FastHttpSession), os.environ['RUN_ID'], int(os.environ['DURATION'].removesuffix('s')))
    locust_main()


if __name__ == '__main__':
    main()
