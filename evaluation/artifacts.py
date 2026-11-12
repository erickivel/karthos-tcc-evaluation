"""Offline evidence extraction and conservative cell scoring."""
from __future__ import annotations

import json
from pathlib import Path

from .protocol import source_id
from .verify import compare_provenance, counter_increase, k6_requests, k6_quality, parse_time


def extract_marked(log: str, kind: str) -> str:
    begin, end = f'__EVAL_{kind}_BEGIN__', f'__EVAL_{kind}_END__'
    left = log.find(begin)
    right = log.find(end, left + len(begin)) if left >= 0 else -1
    if left < 0 or right < 0:
        raise ValueError(f'missing or incomplete {kind} evidence markers')
    return log[left + len(begin):right].lstrip('\n')


def score_count(expected: int, actual: float | None, relative_tolerance: float) -> dict:
    if actual is None:
        return {'state': 'missing', 'expected': expected, 'actual': None}
    error = abs(actual - expected)
    limit = max(2, expected * relative_tolerance)
    return {'state': 'pass' if error <= limit else 'fail', 'expected': expected,
            'actual': actual, 'absolute_error': error, 'allowed_error': limit}


def _read(path: Path, default: dict | None = None) -> dict:
    return json.loads(path.read_text()) if path.exists() else (default or {})


def _ingress_count(samples: list[dict], demo: str) -> float | None:
    target = {'boutique': 'frontend', 'astronomy': 'frontend-proxy'}.get(demo)
    if target is None:
        return None
    selected = []
    for sample in samples:
        labels = sample.get('labels') or {}
        server = str(labels.get('edge_server', ''))
        if target not in server:
            continue
        if labels.get('virtual_node') == 'client':
            continue
        selected.append(sample)
    if not selected:
        return None
    # API returns 15-second evaluation points for each raw series. Sum only
    # series at the newest common evaluation timestamp, never all grid points.
    timed = [s for s in selected if s.get('timestamp')]
    if timed:
        newest = max(s['timestamp'] for s in timed)
        selected = [s for s in timed if s['timestamp'] == newest]
    return sum(float(s['value']) for s in selected)



def _checkout_count(samples: list[dict], demo: str) -> float | None:
    target={'boutique':'checkoutservice','astronomy':'checkout'}.get(demo)
    if target is None:
        return None
    matching=[]
    for sample in samples:
        labels=sample.get('labels') or {}
        if labels.get('edge_server')!=target:
            continue
        if not str(labels.get('edge_client','')).startswith('frontend'):
            continue
        if labels.get('virtual_node'):
            continue
        matching.append(sample)
    if not matching:
        return None
    timed=[s for s in matching if s.get('timestamp')]
    if timed:
        newest=max(s['timestamp'] for s in timed)
        matching=[s for s in timed if s['timestamp']==newest]
    return sum(float(s['value']) for s in matching)


def _checkout_raw_delta(samples: list[dict], demo: str, start: str, end: str) -> dict:
    """Derive a bounded counter delta when increase has no sparse edge series.

    The first and last observed points must be near the measurement boundaries.
    This is a sampled comparison, so return coverage details with the estimate.
    """
    target={'boutique':'checkoutservice','astronomy':'checkout'}.get(demo)
    series={}
    for sample in samples:
        labels=sample.get('labels') or {}
        if (labels.get('edge_server')!=target or
                not str(labels.get('edge_client','')).startswith('frontend') or
                labels.get('virtual_node') or not sample.get('timestamp')):
            continue
        key=json.dumps(labels,sort_keys=True)
        series.setdefault(key,[]).append(sample)
    if not series:
        return {'value':None,'reason':'no checkout raw series'}
    beginning,ending=parse_time(start),parse_time(end)
    total=0.0
    gaps=[]
    for points in series.values():
        ordered=sorted(points,key=lambda s:parse_time(s['timestamp']))
        first,last=parse_time(ordered[0]['timestamp']),parse_time(ordered[-1]['timestamp'])
        start_gap=(first-beginning).total_seconds()
        end_gap=(ending-last).total_seconds()
        gaps.append({'first_sample':ordered[0]['timestamp'],'last_sample':ordered[-1]['timestamp'],
                     'start_gap_seconds':start_gap,'end_gap_seconds':end_gap,'points':len(ordered)})
        if len(ordered)<2 or start_gap>30 or end_gap>30:
            return {'value':None,'reason':'insufficient boundary coverage','series':gaps}
        total+=counter_increase(float(point['value']) for point in ordered)
    return {'value':total,'method':'raw_counter_delta','series':gaps,
            'limitation':'Counts before the first sampled point are excluded.'}


def evaluate_bundle(path: Path, tolerances: dict) -> dict:
    manifest = _read(path / 'manifest.json')
    ledger = k6_requests(path / 'k6.ndjson', manifest['start'], manifest['end'])
    quality = k6_quality(path / 'k6.ndjson')
    response = _read(path / 'api-request-count.json')
    samples = response.get('data', {}).get('samples', [])
    expected_source = source_id(manifest['source'])
    raw_samples=_read(path/'api-request-raw.json').get('data',{}).get('samples',[])
    provenance = compare_provenance(samples + raw_samples, expected_source)
    demo = manifest.get('demo', 'boutique')
    count = _checkout_count(samples, demo)
    count_evidence={'method':'api_increase'}
    if count is None:
        count_evidence=_checkout_raw_delta(raw_samples,demo,manifest['start'],manifest['end'])
        count=count_evidence.get('value')
    ingress = _ingress_count(samples, demo)
    entities = evaluate_entities(path, manifest['namespace']) if manifest.get('namespace') and (path/'api-nodes.json').exists() else None
    runs_on = evaluate_runs_on(path, manifest['namespace']) if manifest.get('namespace') and (path/'api-edges.json').exists() else None
    resources = resource_coverage(path, manifest['end'], manifest.get('namespace'))
    return {
        'entities': entities, 'runs_on_edges': runs_on, 'resources': resources,
        'cell': manifest.get('cell'), 'source': expected_source,
        'requests': {'actual_completed': ledger['total'], 'by_route': ledger['by_route'],
                     'generator_http_errors': ledger['errors'], 'dropped_iterations': quality['dropped_iterations'],
                     'checkout_route_completed': ledger['by_route'].get('checkout',0),
                     'ingress_observed': ingress,
                     'count_evidence': count_evidence,
                     'comparison': score_count(ledger['by_route'].get('checkout',0), count, tolerances['request_relative_tolerance'])},
        'provenance': provenance,
        'state': 'fail' if quality['dropped_iterations'] or provenance['wrong'] or provenance['missing'] or
                 (entities is not None and bool(entities['missing'])) or
                 (runs_on is not None and bool(runs_on['missing'])) or
                 score_count(ledger['by_route'].get('checkout',0), count, tolerances['request_relative_tolerance'])['state'] == 'fail'
                 else ('incomplete' if count is None else 'pass'),
    }


def evaluate_entities(path: Path, namespace: str) -> dict:
    from .verify import compare_entity_sets
    expected=set()
    for filename,kind in [('k8s-pods.json','pod'),('k8s-services.json','service')]:
        for item in _read(path/filename).get('items',[]):
            meta=item.get('metadata',{})
            if kind=='pod' and meta.get('labels',{}).get('evaluation.karthos.dev/owner')=='karthos-evaluation':
                continue
            if meta.get('namespace')==namespace:
                expected.add((kind,namespace,meta['name']))
    harness_pods={item.get('metadata',{}).get('name') for item in _read(path/'k8s-pods.json').get('items',[])
                  if item.get('metadata',{}).get('labels',{}).get('evaluation.karthos.dev/owner')=='karthos-evaluation'}
    actual=set()
    for node in _read(path/'api-nodes.json').get('data',{}).get('nodes',[]):
        if node.get('namespace')==namespace and node.get('kind') in ('pod','service'):
            if node.get('kind')=='pod' and node.get('name') in harness_pods:
                continue
            actual.add((node['kind'],namespace,node['name']))
    return compare_entity_sets(expected,actual)


def evaluate_runs_on(path: Path, namespace: str) -> dict:
    from .verify import compare_entity_sets
    nodes = _read(path/'api-nodes.json').get('data',{}).get('nodes',[])
    ids = {(node.get('kind'),node.get('namespace') or '',node.get('name')):node.get('id') for node in nodes}
    expected=set()
    unresolved=[]
    for pod in _read(path/'k8s-pods.json').get('items',[]):
        meta=pod.get('metadata',{})
        if meta.get('labels',{}).get('evaluation.karthos.dev/owner')=='karthos-evaluation':
            continue
        if meta.get('namespace')!=namespace:
            continue
        node_name=pod.get('spec',{}).get('nodeName')
        if not node_name:
            continue
        source=ids.get(('pod',namespace,meta['name']))
        target=ids.get(('node','',node_name))
        if source and target:
            expected.add((source,target,'runs_on'))
        else:
            unresolved.append({'pod':meta['name'],'node':node_name})
    actual={(edge.get('source'),edge.get('target'),'runs_on') for edge in
            _read(path/'api-edges.json').get('data',{}).get('edges',[])
            if edge.get('relation')=='runs_on' and any(edge.get('source')==e[0] for e in expected)}
    result=compare_entity_sets(expected,actual)
    result['unresolved_entities']=unresolved
    return result


def resource_coverage(path: Path, end: str, namespace: str | None = None) -> dict:
    from .verify import parse_time
    ids=None
    if namespace and (path/'api-nodes.json').exists():
        ids={n['id'] for n in _read(path/'api-nodes.json').get('data',{}).get('nodes',[])
             if n.get('namespace')==namespace and n.get('kind')=='container'}
    result={}
    for name in ('cpu','memory'):
        samples=_read(path/f'api-{name}.json').get('data',{}).get('samples',[])
        if ids is not None:
            samples=[sample for sample in samples if sample.get('entity_id') in ids]
        provenance=compare_provenance(samples,'kubelet')
        timestamps=[parse_time(sample['timestamp']) for sample in samples if sample.get('timestamp')]
        lag=(parse_time(end)-max(timestamps)).total_seconds() if timestamps else None
        state='missing' if not samples else 'wrong_source' if provenance['wrong'] or provenance['missing'] else 'stale' if lag is not None and lag>60 else 'present'
        result[name]={'state':state,'sample_count':len(samples),'distinct_entities':len({s.get('entity_id') for s in samples}),
                      'freshness_lag_seconds':lag,'source':provenance}
    return result
