"""Read-only pre-traffic guard for pinned upstream native generator identities.

Identity evidence: cache/boutique/helm-chart/{values.yaml,templates/loadgenerator.yaml}
and cache/opentelemetry-demo/templates/{component.yaml,_helpers.tpl,_objects.tpl}.
No substring heuristics, metric label changes, or inferred background counts.
"""
from pathlib import Path
import time

IDENTITIES = {
    'boutique': ('loadgenerator', {'app': 'loadgenerator'}),
    'astronomy': ('load-generator', {'app.kubernetes.io/component': 'load-generator'}),
}


def inspect(demo, deployments, pods):
    name, labels = IDENTITIES[demo]
    result = {'state': 'insufficient', 'demo': demo, 'known_generator_name': name,
              'known_generator_labels': labels, 'all_traffic_attributed': False,
              'scope': 'known pinned upstream generators stopped; not proof of absence of unknown/background traffic',
              'declared_probe_routes': [], 'declared_background_routes': [],
              'excluded_request_count': None,
              'exclusion_policy': 'health probes excluded from declared business journeys only; no telemetry rows or unknown calls suppressed',
              'request_identity_policy': 'existing journey/request IDs unchanged; no high-cardinality metric labels added'}
    for document in (deployments, pods):
        if not isinstance(document, dict) or not isinstance(document.get('items'), list) or any(not isinstance(row, dict) or not row.get('metadata', {}).get('name') for row in document['items']):
            return dict(result, reason='complete named deployment and Pod inventory required')
    def known(row):
        meta = row['metadata']
        return meta['name'] == name or all(meta.get('labels', {}).get(key) == value for key, value in labels.items())
    selected = [row for row in deployments['items'] if known(row)]
    active = [row['metadata']['name'] for row in pods['items'] if known(row) and row.get('status', {}).get('phase') not in ('Succeeded', 'Failed')]
    blocked = []
    for row in selected:
        status = row.get('status', {})
        generation = row['metadata'].get('generation')
        if (row.get('spec', {}).get('replicas', 1) != 0
                or any(status.get(key, 0) != 0 for key in ('replicas', 'readyReplicas', 'availableReplicas', 'updatedReplicas'))
                or (generation is not None and status.get('observedGeneration', -1) < generation)):
            blocked.append(row['metadata']['name'])
    for pod in pods['items']:
        for container in pod.get('spec', {}).get('containers', []):
            for key in ('livenessProbe', 'readinessProbe', 'startupProbe'):
                request = container.get(key, {}).get('httpGet')
                if request:
                    result['declared_probe_routes'].append({'pod': pod['metadata']['name'], 'container': container.get('name'),
                        'probe': key, 'path': request.get('path'), 'port': request.get('port'),
                        'observed_request_count': None, 'basis': 'PodSpec HTTP probe declaration; not an observed request'})
    result.update(state='fail' if blocked or active else 'pass', blocked_deployments=blocked,
                  active_known_generator_pods=active,
                  known_generator_deployments=[row['metadata']['name'] for row in selected],
                  unclassified_deployments=[row['metadata']['name'] for row in deployments['items'] if not known(row)])
    return result


def verify(ctx, cell, path):
    """Run before every traffic attempt, including reused deployments; never scale."""
    started = time.time()
    try:
        deployments = ctx.json('get', 'deployments', '-n', cell.namespace)
        pods = ctx.json('get', 'pods', '-n', cell.namespace)
        result = inspect(cell.demo, deployments, pods)
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
        result = {'state': 'insufficient', 'reason': str(exc)}
    result.update(namespace=cell.namespace, acquisition_started_at=started, acquisition_ended_at=time.time(),
                  timestamp_semantics='sequential inventory acquisition bracket, not an atomic Kubernetes snapshot')
    ctx.write(Path(path) / 'traffic-population-preflight.json', result)
    if result['state'] != 'pass':
        raise ValueError('native traffic population preflight did not pass: ' + result.get('reason', 'known generator still active or not reconciled'))
    return result
