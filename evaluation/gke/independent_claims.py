"""Explicit, bounded claims against independent retained observations."""
from pathlib import Path
from .models import read
from .thesis_comparison import _observation
from ..scoring import compare, DEFAULTS


def structural_fidelity(path, namespace):
    """Check declared services, pods, containers and placement, not all 11 kinds.

    Retained export inventories are sequential observations, not atomic snapshots.
    Extra graph nodes can be retained history and are reported, not suppressed.
    """
    result = dict(state='insufficient', all_entity_kinds_verified=False,
        scope='declared services, pods, containers, contains and runs_on at export',
        limitations=['sequential inventory/API acquisition; not an atomic snapshot',
                     'workloads, ingress, configuration and volumes require additional independent inventories',
                     'Kubernetes UIDs retained as reference provenance; canonical IDs are logical identities'])
    checks=[]
    try:
        path=Path(path)
        names=('k8s-services.json','k8s-pods.json','k8s-nodes.json','api-nodes.json','api-edges.json')
        docs={name:read(path/name) for name in names}
        if not namespace:raise ValueError('namespace unavailable')
        inventory={kind:docs['k8s-'+kind+'.json']['items'] for kind in ('services','pods','nodes')}
        if any(not isinstance(items,list) for items in inventory.values()):raise ValueError('incomplete inventory')
        graph=docs['api-nodes.json']['data']['nodes'];edges=docs['api-edges.json']['data']['edges']
        checks=[];matched=set()
        def entity(kind,name,ns=None,pod=None):
            found=[node for node in graph if node.get('kind')==kind and node.get('name')==name
                   and (ns is None or node.get('namespace')==ns)
                   and (pod is None or node.get('labels',{}).get('k8s.pod.name')==pod)]
            checks.append(dict(kind=kind,name=name,namespace=ns, pod=pod,
                               state='pass' if len(found)==1 and found[0].get('id') else 'fail',matches=len(found)))
            if len(found)==1 and found[0].get('id'):
                matched.add(found[0]['id']);return found[0]['id']
        def relation(source,target,kind):
            found=[e for e in edges if e.get('source')==source and e.get('target')==target and e.get('relation')==kind]
            checks.append(dict(relation=kind,source=source,target=target,state='pass' if source and target and len(found)==1 else 'fail'))
        services=[obj for obj in inventory['services'] if obj['metadata'].get('namespace')==namespace]
        pods=[obj for obj in inventory['pods'] if obj['metadata'].get('namespace')==namespace]
        if not services or not pods:raise ValueError('application pod/service inventory empty')
        for obj in services:
            entity('service',obj['metadata']['name'],namespace)
        node_names={obj['metadata']['name'] for obj in inventory['nodes']}
        for obj in pods:
            meta=obj['metadata'];spec=obj['spec']
            if not meta.get('uid'):
                checks.append(dict(state='insufficient',pod=meta.get('name'),reason='Pod UID unavailable'));continue
            if meta.get('deletionTimestamp') or obj.get('status',{}).get('phase') in ('Succeeded','Failed'):
                checks.append(dict(state='insufficient',pod=meta.get('name'),reason='terminal/terminating pod requires time-matched lifecycle evidence'));continue
            pod=entity('pod',meta['name'],namespace)
            checks[-1]['reference_uid']=meta['uid']
            if spec.get('nodeName') not in node_names:
                checks.append(dict(state='insufficient',pod=meta.get('name'),reason='pod placement lacks independent Node inventory'));continue
            node=entity('node',spec['nodeName'])
            relation(pod,node,'runs_on')
            placements=[e for e in edges if e.get('source')==pod and e.get('relation')=='runs_on']
            if any(e.get('target')!=node for e in placements):
                checks.append(dict(state='fail',reason='contradictory pod placement',pod=pod))
            for container in spec.get('containers',[]):
                child=entity('container',container['name'],namespace,meta['name'])
                relation(pod,child,'contains')
        return dict(result,state='fail' if any(c['state']=='fail' for c in checks) else 'insufficient' if any(c['state']=='insufficient' for c in checks) else 'pass',
                    checks=checks,artifacts=list(names),
                    unmatched_graph_ids=sorted(n['id'] for n in graph if n.get('namespace')==namespace and n.get('id') and n['id'] not in matched))
    except (OSError,ValueError,KeyError,TypeError) as exc:
        return dict(result,state='fail' if any(c['state']=='fail' for c in checks) else 'insufficient',checks=checks,reason=str(exc))


def single_contribution(report, thresholds=None):
    result=dict(state='insufficient',scope='selected-source settled checkout population',
                limitation='aggregate evidence cannot exclude compensating loss and duplication or prove every request identity')
    selection=report.get('source_selection',{})
    if (selection.get('state')=='fail' or (selection.get('state')=='pass'
            and report.get('manifest',{}).get('source_id')!=selection.get('expected_source'))):
        return dict(result,state='fail',reason='selected source contradicts emitted provenance')
    population=_observation(report)
    if population is None:return dict(result,reason='independent population, provenance or evidence prerequisites unavailable')
    thresholds={**DEFAULTS,**(thresholds or {})}
    comparison=compare(population['reference_calls'],population['settled_calls'],
                       thresholds['request_relative_tolerance'],thresholds['request_absolute_tolerance'])
    return dict(result,**comparison,population=population,unit='requests')


def no_double_counting(matrix, exported, thresholds=None):
    checks=[];artifacts=[]
    for spec in matrix:
        if spec.get('phase')!='arbitration' or spec.get('scenario')!='both-sources':continue
        record=exported.get(spec['id'])
        if record is None:
            checks.append(dict(state='insufficient',reason='both-available evidence absent'));continue
        uri,data=record;artifacts.append(uri)
        checks.append(single_contribution(data,thresholds))
        checks.append(dict(state=data.get('families',{}).get('traffic_count',{}).get('state','insufficient')))
        checks.append(dict(state='pass' if data.get('source_selection',{}).get('expected_source')=='mesh.istio' else 'fail'))
    states=[row['state'] for row in checks]
    verdict='fail' if 'fail' in states else 'pass' if states and all(s=='pass' for s in states) else 'insufficient'
    return dict(claim='no-double-counting',verdict=verdict,checks=checks,artifacts=artifacts,
                reference='both-available scenario, captured selected source and independent checkout totals',
                limitation='bounded aggregate assertion; compensating loss and duplication cannot be ruled out')
