"""Explicit comparison identities. Unknown dimensions are retained, never guessed away."""
from __future__ import annotations
import json
from datetime import datetime

# Storage metadata has no API label equivalent. No source dimensions are discarded.
STORED_ONLY = frozenset({'__name__', 'entity_id', 'karthos_unit'})
ALIASES = {'http_status_code': 'http.response.status_code'}


def dimensions(labels):
    out = {}
    for key, value in labels.items():
        if key in STORED_ONLY:
            continue
        key = ALIASES.get(key, key)
        value = str(value)
        if key in out and out[key] != value:
            raise ValueError('conflicting dimension aliases: ' + key)
        out[key] = value
    return out


def partition_key(labels):
    return json.dumps(dimensions(labels), sort_keys=True, separators=(',', ':'))


def request_failed(labels):
    """Version 1 policy. None is unknown, not a successful request."""
    grpc = labels.get('rpc.grpc.status_code', labels.get('grpc_response_status'))
    protocol = labels.get('network.protocol.name', labels.get('request_protocol', '')).lower()
    if grpc not in (None, '', '-'):
        try:
            return int(grpc) != 0
        except (ValueError, TypeError):
            return None
    if protocol == 'grpc':
        return None
    code = labels.get('http.response.status_code', labels.get('http_status_code', labels.get('response_code', labels.get('status'))))
    try:
        code = int(code)
    except (ValueError, TypeError):
        return None
    return 500 <= code <= 599 if 100 <= code <= 599 else None


def container_lifecycle(pods, start, end):
    result = {}
    for pod in pods:
        meta, spec, status = pod.get('metadata', {}), pod.get('spec', {}), pod.get('status', {})
        job = any(x.get('kind') == 'Job' for x in meta.get('ownerReferences', []))
        statuses = {x['name']: x for key in ('containerStatuses', 'initContainerStatuses') for x in status.get(key, [])}
        for kind in ('containers', 'initContainers'):
            for container in spec.get(kind, []):
                key = (meta.get('namespace'), meta.get('name'), container['name'])
                lifecycle = statuses.get(container['name'], {})
                terminated = lifecycle.get('state', {}).get('terminated', {})
                finished = terminated.get('finishedAt')
                finished_before = False
                if finished:
                    finished_before = datetime.fromisoformat(finished.replace('Z', '+00:00')).timestamp() <= start
                init = kind == 'initContainers'
                sidecar = init and container.get('restartPolicy') == 'Always'
                category = 'sidecar' if sidecar else ('init' if init else ('job' if job else 'application'))
                result[key] = dict(category=category, expected=not finished_before,
                                   restarts=lifecycle.get('restartCount', 0), finished_at=finished,
                                   reason='completed before measurement' if finished_before else 'active or lifecycle unknown')
    return result
