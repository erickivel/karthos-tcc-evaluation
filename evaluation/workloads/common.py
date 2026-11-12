"""Matched journey contract, also consumed directly by k6 as workload.json.

Single-process Locust assigns iteration IDs globally. Sequence is deterministic;
arrival timestamps remain generator-specific and are never claimed equivalent.
"""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode

CONFIG = json.loads(Path(__file__).with_name('workload.json').read_text())


def _expand(value, user):
    if isinstance(value, str):
        return value.replace('{user}', user)
    if isinstance(value, dict):
        return {k: _expand(v, user) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v, user) for v in value]
    return value


def synthetic_user(run_id, iteration):
    """Bounded valid email local part and cart key; full run ID stays in evidence."""
    return f'eval-{hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:12]}-{iteration}'


def journey(demo, workload, iteration, run_id):
    schedule = CONFIG['workloads'][workload]['schedule']
    contract = CONFIG['demos'][demo]
    steps = _expand(contract['journeys'][schedule[iteration % len(schedule)]], synthetic_user(run_id, iteration))
    for step in steps:
        step['headers'] = _expand(contract['headers'], synthetic_user(run_id, iteration))
        if step['body'] is not None:
            step['headers']['Content-Type'] = ('application/x-www-form-urlencoded' if contract['encoding']=='form' else 'application/json')
            if contract['encoding']=='form':
                step['body'] = urlencode(step['body'])
    return steps


def outcome_ok(step, status, text):
    if status != step['expected_status']:
        return False
    outcome = step['outcome']
    if outcome == 'status':
        return True
    if outcome == 'boutique_order':
        return 'Your order is complete!' in text
    if outcome == 'expired':
        return 'expired' in text.lower() and 'failed to complete the order' in text.lower()
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return False
    if not isinstance(data, dict):
        return False
    if outcome == 'payment_failed':
        return data.get('code') == 'PAYMENT_FAILED'
    if outcome == 'cart_item':
        return any(i.get('productId') == CONFIG['product_id'] and i.get('quantity') == 1 for i in data.get('items', []))
    if outcome == 'astronomy_order':
        return bool(data.get('orderId')) and any(i.get('item', {}).get('productId') == CONFIG['product_id'] and i.get('item', {}).get('quantity') == 1 for i in data.get('items', []))
    return False


def window_record(run_id, generator, started_at, ended_at, duration_seconds, rate):
    return dict(run_id=run_id, generator=generator, started_at=started_at,
                ended_at=ended_at, intended_duration_seconds=duration_seconds,
                target_iterations_per_second=rate,
                expected_iterations=duration_seconds * rate)


def trace_context(request_id):
    trace_id = hashlib.sha256(request_id.encode()).hexdigest()[:32]
    parent_id = hashlib.sha256((request_id+':client').encode()).hexdigest()[:16]
    return {'trace_id': trace_id, 'traceparent': f'00-{trace_id}-{parent_id}-01'}
