"""Score independent local fixture records and freeze a checksummed receipt."""
from __future__ import annotations

import argparse
from pathlib import Path

from ..gke.models import THESIS, read, sha, write
from ..gke.thesis import LOCAL_CHECKS
from ..runner import repository_fingerprint

KINDS = frozenset(('node', 'namespace', 'workload', 'pod', 'container', 'service',
                   'ingress', 'configmap', 'secret', 'pvc', 'hpa'))
RELATIONS = frozenset(('calls', 'runs_on', 'contains', 'routes_to', 'exposes', 'uses', 'mounts'))


def kind_structure(gateway_pod, replica_set, replacement, graph, *, mesh_hpa='istiod'):
    """Declare expected edges from the independent fixture and Kubernetes inventory."""
    namespace = 'karthos-demo'
    pod_meta = gateway_pod['metadata']
    rs_meta = replica_set['metadata']
    if not any(owner['uid'] == rs_meta['uid'] and owner.get('controller')
               for owner in pod_meta.get('ownerReferences', [])):
        raise ValueError('fixture Pod does not belong to the independently observed ReplicaSet')
    if not any(owner['name'] == 'gateway-go' and owner['kind'] == 'Deployment' and owner.get('controller')
               for owner in rs_meta.get('ownerReferences', [])):
        raise ValueError('fixture ReplicaSet does not belong to the declared Deployment')
    fields = (
        ('ns','namespace',namespace,namespace),
        ('gateway','workload','gateway-go',namespace),
        ('rs','workload',rs_meta['name'],namespace),
        ('pod','pod',pod_meta['name'],namespace),
        ('container','container','gateway-go',namespace),
        ('node','node',gateway_pod['spec']['nodeName'],''),
        ('service','service','gateway-go',namespace),
        ('callee','service','service-a-python',namespace),
        ('ingress','ingress','gateway-ingress',namespace),
        ('bundle','workload','config-bundle',namespace),
        ('config','configmap','bundle-config',namespace),
        ('secret','secret','bundle-secret',namespace),
        ('volume','pvc','bundle-cache',namespace),
        ('hpa','hpa',mesh_hpa,'istio-system'),
    )
    edges = (
        ('ns','contains','gateway'),('ns','contains','rs'),('ns','contains','bundle'),
        ('gateway','contains','rs'),('rs','contains','pod'),('pod','contains','container'),
        ('pod','runs_on','node'),('service','exposes','gateway'),('service','calls','callee'),
        ('ingress','routes_to','service'),('bundle','uses','config'),('bundle','uses','secret'),
        ('bundle','mounts','volume'),
    )
    entities = {role:dict(kind=kind,name=name,namespace=scope) for role,kind,name,scope in fields}
    return {'expected':{'entities':entities,'edges':edges},'observed':graph,'replacement':replacement}


def structure(data):
    fixture = data['expected']
    graph = data['observed']
    nodes = {}
    for name, selector in fixture['entities'].items():
        matches = [row for row in graph['nodes'] if all(row.get(k, '') == v for k, v in selector.items())]
        if len(matches) != 1:
            return {'state': 'fail', 'unresolved_entity': name, 'matches': len(matches)}
        nodes[matches[0]['id']] = name
    expected = {tuple(edge) for edge in fixture['edges']}
    observed = {(nodes[e['source']], e['relation'], nodes[e['target']]) for e in graph['edges']
                if e['source'] in nodes and e['target'] in nodes}
    kinds = {row['kind'] for row in fixture['entities'].values()}
    relations = {row[1] for row in expected}
    old, new = data['replacement']
    def placement_present(row):
        return any(edge['source'] == row['canonical_id'] and edge['target'] == row['node_id']
                   and edge['relation'] == 'runs_on' and edge.get('last_seen')
                   for edge in row['placement_edges'])
    replacement = bool(old['uid'] and new['uid'] and old['uid'] != new['uid']
        and old['canonical_id'] == new['canonical_id'] and old['name'] == new['name']
        and old['node_id'] != new['node_id'] and old['snapshot'] != new['snapshot']
        and placement_present(old) and placement_present(new))
    return {'state': 'pass' if expected == observed and kinds == KINDS and relations == RELATIONS and replacement else 'fail',
            'missing_edges': sorted(expected - observed), 'unexpected_edges': sorted(observed - expected),
            'missing_kinds': sorted(KINDS - kinds), 'missing_relations': sorted(RELATIONS - relations),
            'replacement_verified': replacement}


def temporal(data):
    first, quiet, next_traffic = data['counter_and_edge_observations']
    stable = (first['counter'] == quiet['counter'] < next_traffic['counter']
              and first['last_seen'] == quiet['last_seen'] < next_traffic['last_seen']
              and first['scrape_at'] < quiet['scrape_at'] < next_traffic['scrape_at'])
    additional = ('source-filtered-history', 'reset', 'expiry', 'retained-replacement')
    return {'state': 'pass' if stable and all(data['checks'].get(key) is True for key in additional) else 'fail',
            'quiet_stability_and_new_activity': stable, 'additional': {key:data['checks'].get(key) for key in additional}}


def semantics(data):
    # The existing integration fixture is scored by its assertions. Record
    # each named subcase from its actual test output, including counter shape.
    required = ('grpc-failure', 'grpc-success', 'grpc-unknown', 'http-client', 'http-server',
                'grpc-precedence', 'counter-reset-temporal', 'delta-temporal', 'edge-restrictions',
                'native-source-and-provenance', 'canonical-resource-roundtrip')
    test = 'TestQueryViewsVictoriaMetricsIntegration'
    lines = data.splitlines()
    def passed(name):
        return any(line.strip().startswith('--- PASS: '+name+' (') for line in lines)
    missing = [name for name in required if not passed(test+'/'+name)]
    for source in ('mesh.istio','traces.servicegraph'):
        if not any(line.startswith('PASS exact-duration '+source+' ') for line in lines):
            missing.append('exact-duration '+source)
    return {'state': 'pass' if passed(test) and not missing else 'insufficient', 'missing_subcases': missing}


def arbitration(data):
    mesh, trace = data['stages']
    states = (set(mesh['available_sources']) == {'mesh.istio','traces.servicegraph'},
              set(trace['available_sources']) == {'traces.servicegraph'},
              mesh['configuration_sha256'] != trace['configuration_sha256'],
              trace['explicit_rediscovery'] is True and trace['explicit_redeployment'] is True)
    metrics = {'servicegraph.request.count', 'servicegraph.request.error.count', 'servicegraph.request.duration'}
    selected = all(set(row['metric_contributions']) == metrics and
                   all(set(sources) == {source} for sources in row['metric_contributions'].values())
                   for row,source in ((mesh,'mesh.istio'),(trace,'traces.servicegraph')))
    populations = (mesh['reference_calls'] == trace['reference_calls'] > 0
                   and mesh['observed_calls'] == trace['observed_calls'] == trace['reference_calls'])
    return {'state': 'pass' if all(states) and selected and populations else 'fail',
            'single_selected_source': selected, 'equivalent_traffic_population': populations}


def api_only(data):
    keys = {'interval-callers','current-replicas-and-placement','retained-replicas-and-health',
            'container-resource-history','request-error-derivation'}
    answers = data['answers']
    complete = set(answers) == keys and data['openapi_validated'] is True
    for answer in answers.values():
        complete = complete and answer['state'] == 'correct' and answer['expected'] is not None and answer['expected'] == answer['observed']
    return {'state': 'pass' if complete else 'insufficient', 'answers': answers}


CHECKS = {
    'structure': ('structure.json', structure),
    'source-semantics': ('source-semantics.log', semantics),
    'arbitration': ('arbitration.json', arbitration),
    'temporal-history': ('temporal-history.json', temporal),
    'api-only': ('api-only.json', api_only),
}


def qualify(root, pins):
    """Score a new attempt under the active protocol.

    Existing v2 receipts remain immutable and can be validated directly by
    local_contract for identical product pins; reuse does not rescore or relabel
    the original qualification.
    """
    root = Path(root)
    result = {}
    for name in LOCAL_CHECKS:
        filename, verify = CHECKS[name]
        path = root/filename
        row = {'state': 'insufficient', 'reference': filename}
        if path.is_file():
            row['sha256'] = sha(path)
            try:
                row.update(verify(path.read_text() if path.suffix == '.log' else read(path)))
            except (KeyError, TypeError, ValueError) as exc:
                row['reason'] = 'malformed evidence: '+str(exc)
        else:
            row['reason'] = 'required local record is unavailable'
        result[name] = row
    return {'protocol': THESIS, 'revision': pins['karthos_revision'],
            'source_sha256': pins['karthos_source_hash'], 'checks': result}


def main():
    import subprocess
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--product', type=Path, required=True)
    parser.add_argument('--product-lock', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    pins = read(args.product_lock)
    revision = subprocess.check_output(['git','rev-parse','HEAD'],cwd=args.product,text=True).strip()
    if revision != pins['karthos_revision'] or repository_fingerprint(args.product) != pins['karthos_source_hash']:
        parser.error('checkout differs from the product lock')
    receipt = qualify(args.evidence, pins)
    destination = args.evidence/'local-contract.json'
    if destination.exists():
        parser.error('receipt exists; keep it and use a new attempt directory')
    write(destination, receipt)
    return 0 if all(row['state'] == 'pass' for row in receipt['checks'].values()) else 2


if __name__ == '__main__':
    raise SystemExit(main())
