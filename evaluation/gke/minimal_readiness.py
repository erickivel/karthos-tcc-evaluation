"""Read-only per-cell deployment/connectivity observations; never generates traffic."""
from __future__ import annotations

import json
import time
from urllib.parse import urlencode
from urllib.request import urlopen

from . import export
from .models import read
from ..protocol import source_id

OBSERVATION_SECONDS = 120
RESPONSE_BYTES = 4 * 1024 * 1024


class MinimalReadinessSafetyError(RuntimeError):
    """Required readiness evidence could not be preserved or exported."""


def _json(base, route, params, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('minimal readiness observation budget exhausted')
    with urlopen(base + route + '?' + urlencode(params), timeout=min(10, remaining)) as response:
        raw = response.read(RESPONSE_BYTES + 1)
    if len(raw) > RESPONSE_BYTES:
        raise ValueError('readiness API response exceeds byte bound')
    payload = json.loads(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get('data'), dict):
        raise ValueError('readiness API response lacks data object')
    return payload


def _pod_state(payload):
    pods = [pod for pod in payload.get('items', [])
            if not pod.get('metadata', {}).get('deletionTimestamp')
            and pod.get('status', {}).get('phase') not in ('Succeeded', 'Failed')]
    not_ready = [pod.get('metadata', {}).get('name') for pod in pods
                 if not any(item.get('type') == 'Ready' and item.get('status') == 'True'
                            for item in pod.get('status', {}).get('conditions', []))]
    return {'valid': bool(pods) and not not_ready, 'active_pods': len(pods), 'not_ready': not_ready}



def _rollout_state(payload, required=()):
    controllers = [item for item in payload.get('items', [])
                   if not item.get('metadata', {}).get('deletionTimestamp')]
    rows = []
    for item in controllers:
        meta, status, spec = item.get('metadata', {}), item.get('status', {}), item.get('spec', {})
        kind = item.get('kind')
        generation = meta.get('generation')
        observed = status.get('observedGeneration')
        generation_ready = type(generation) is int and type(observed) is int and observed >= generation
        if kind == 'DaemonSet':
            desired = status.get('desiredNumberScheduled')
            ready = (type(desired) is int and desired > 0 and status.get('updatedNumberScheduled') == desired
                     and status.get('numberAvailable') == desired and status.get('numberUnavailable', 0) == 0)
        else:
            desired = spec.get('replicas', 1)
            ready = (kind in ('Deployment', 'StatefulSet') and type(desired) is int and desired > 0
                     and status.get('updatedReplicas') == desired and status.get('readyReplicas') == desired
                     and status.get('replicas') == desired)
            if kind == 'Deployment':
                ready = ready and status.get('availableReplicas') == desired
        rows.append({'name': meta.get('name'), 'kind': kind, 'generation': generation,
                     'observed_generation': observed, 'desired': desired, 'ready': generation_ready and ready})
    missing = sorted(set(required) - {row['name'] for row in rows})
    return {'valid': bool(rows) and not missing and all(row['ready'] for row in rows),
            'controllers': rows, 'missing_components': missing}


def check_cell(ctx, driver, spec, attempt_id):
    """Collect independent observations, seal a receipt, return usability.

    The 120-second cap bounds observation work, not the existing verified export
    path. Failed core observations do not raise; the caller decides whether the
    deployment can be attempted again. No traffic-series presence is required.
    """
    from .. import runner as r

    if not isinstance(attempt_id, str) or not attempt_id or any(
            char not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for char in attempt_id):
        raise ValueError('invalid minimal readiness attempt identity')
    path = ctx.result / 'readiness' / attempt_id
    path.mkdir(parents=True, exist_ok=False)
    started = time.time()
    remaining = max(0, ctx.deadline - started - ctx.config['limits']['cleanup_reserve_seconds'])
    deadline = time.monotonic() + min(OBSERVATION_SECONDS, remaining)
    checks = {}

    def check(name, observe, *, required=True):
        began = time.time()
        try:
            if time.monotonic() >= deadline:
                raise TimeoutError('minimal readiness observation budget exhausted')
            evidence = observe()
            valid = evidence.pop('_valid', True)
            checks[name] = {'state': 'passed' if valid else 'failed' if required else 'unavailable',
                            'required': required, 'started_at': began, 'finished_at': time.time(),
                            'evidence': evidence}
        except Exception as exc:
            checks[name] = {'state': 'failed' if required else 'unavailable', 'required': required,
                            'started_at': began, 'finished_at': time.time(),
                            'error_type': type(exc).__name__, 'error': export.redact_text(str(exc))}

    def pods(namespace):
        timeout = min(10, max(.001, deadline-time.monotonic()))
        payload = json.loads(ctx.kube('get', 'pods', '-n', namespace, '-o', 'json', timeout=timeout).stdout)
        state = _pod_state(payload)
        if namespace == 'karthos-system':
            names = [pod.get('metadata', {}).get('name', '') for pod in payload.get('items', [])
                     if not pod.get('metadata', {}).get('deletionTimestamp')]
            state['missing_components'] = [name for name in ('karthos-backend', 'karthos-gateway', 'karthos-agent')
                                           if not any(pod.startswith(name + '-') for pod in names)]
            state['valid'] = state['valid'] and not state['missing_components']
        return {'_valid': state['valid'], 'summary': state, 'response': payload}

    check('application-deployment', lambda: pods(driver.cell.namespace))
    check('karthos-deployment', lambda: pods('karthos-system'))

    def rollout(namespace, required=()):
        timeout = min(10, max(.001, deadline-time.monotonic()))
        payload = json.loads(ctx.kube('get', 'deployments,statefulsets,daemonsets', '-n', namespace,
                                     '-o', 'json', timeout=timeout).stdout)
        state = _rollout_state(payload, required)
        return {'_valid': state['valid'], 'summary': state, 'response': payload}

    check('application-rollout', lambda: rollout(driver.cell.namespace))
    check('karthos-rollout', lambda: rollout('karthos-system', ('karthos-backend', 'karthos-gateway', 'karthos-agent')))

    def selection():
        plan = read(driver.setup_path / 'resolved-plan.json')
        timeout = min(10, max(.001, deadline-time.monotonic()))
        contract = json.loads(ctx.kube('get', 'configmap', 'karthos-source-contract', '-n', 'karthos-system',
                                      '-o', 'json', timeout=timeout).stdout)
        mounted = json.loads(contract['data']['source-plan.json'])
        actual = mounted.get('metric_assignments', {}).get('servicegraph.request.count')
        expected = source_id(spec['source'])
        return {'_valid': actual == expected and mounted == plan, 'actual': actual, 'expected': expected,
                'local_plan_matches_mounted': mounted == plan, 'configuration': plan,
                'mounted_configuration': mounted, 'configmap_identity': contract.get('metadata', {})}

    check('source-configuration', selection)
    previous_deadline = r._DEADLINE
    r._DEADLINE = min(previous_deadline, deadline) if previous_deadline is not None else deadline
    try:
        try:
            with r.forward('deployment/karthos-backend', 'karthos-system', 28983, 8080) as base:
                def endpoint(route, key):
                    payload = _json(base, route, {'page_size': 100}, deadline)
                    rows = payload['data'].get(key)
                    return {'_valid': isinstance(rows, list) and bool(rows), 'response': payload,
                            'coverage': 'bounded first page; no completeness claim'}
                check('catalog', lambda: endpoint('/api/v1/metrics/catalog', 'metrics'))
                check('graph', lambda: endpoint('/api/v1/graph/nodes', 'nodes'))

                def telemetry():
                    at = r.utc()
                    payload = _json(base, '/api/v1/metrics', {'metric_name': 'container.cpu.usage',
                                    'aggregation': 'latest', 'from': at, 'to': at, 'page_size': 100}, deadline)
                    samples = payload['data'].get('samples')
                    return {'_valid': isinstance(samples, list) and bool(samples), 'response': payload,
                            'interpretation': 'resource telemetry presence only; no accuracy or traffic claim'}
                check('available-telemetry', telemetry, required=False)
        except Exception as exc:
            for name, required in [('catalog', True), ('graph', True), ('available-telemetry', False)]:
                if name not in checks:
                    checks[name] = {'state': 'failed' if required else 'unavailable', 'required': required,
                                    'error_type': type(exc).__name__, 'error': export.redact_text(str(exc))}
    finally:
        r._DEADLINE = previous_deadline

    report = {'version': 1, 'run_id': ctx.run_id, 'attempt_id': attempt_id,
              'cell': spec['cell'], 'repeat': spec['repeat'], 'started_at': started, 'finished_at': time.time(),
              'observation_seconds': OBSERVATION_SECONDS, 'checks': checks,
              'usable': all(row['state'] == 'passed' for row in checks.values() if row['required']),
              'accuracy_scored': False, 'traffic_generated': False}
    ctx.write(path / 'receipt.json', report)
    try:
        receipt = export.upload(ctx, path, bucket=ctx.state['terraform']['foundation']['evidence_bucket'],
                                prefix='gke-qualification-v1/runs/' + ctx.run_id + '/readiness/' + attempt_id)
        if receipt.get('verified') is not True:
            raise ValueError('minimal readiness export readback did not verify')
    except Exception as exc:
        raise MinimalReadinessSafetyError('minimal readiness evidence export failed: ' + export.redact_text(str(exc))) from exc
    report['export'] = receipt
    report['evidence'] = str(path / 'receipt.json')
    return report
