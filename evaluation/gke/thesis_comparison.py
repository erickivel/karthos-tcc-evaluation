"""Cross-source and clean-deployment comparisons over sealed measured evidence."""
from collections import Counter
import math
from pathlib import Path

from .models import read
from ..scoring import DEFAULTS,compare
from ..timing_analysis import linked_checkout_traces


def _verdict(states):
    return 'fail' if 'fail' in states else 'pass' if states and all(s=='pass' for s in states) else 'insufficient'


def _observation(data):
    report=data.get('traffic',{}).get('report',{})
    observed=report.get('windows',{}).get('whole',{}).get('checkout_place_order',{})
    source=data.get('source_selection',{})
    eventual=report.get('eventual_completeness',{})
    selected=source.get('expected_source')
    if (data.get('evidence',{}).get('valid') is not True or source.get('state')!='pass'
            or selected not in ('traces.servicegraph','mesh.istio') or data.get('manifest',{}).get('source_id')!=selected
            or not observed.get('evidence_present') or eventual.get('state')!='available'
            or report.get('recorder_identity_valid') is not True):
        return None
    reference=observed.get('completion',{}).get('requests')
    stored=eventual.get('requests')
    if type(reference) is not int or reference<=0 or type(stored) not in (int,float) or not math.isfinite(stored) or stored<0:
        return None
    linked=linked_checkout_traces(report) if selected=='traces.servicegraph' else None
    if selected=='traces.servicegraph' and not linked:
        return None
    return {'reference_calls':reference,'settled_calls':stored,'ratio':stored/reference,
            'window_scope':'settled source counters versus independent checkout completion population',
            'selected_source':selected,'linked_checkout_trace_ids':linked,
            'differences':eventual.get('numerical_differences')}


def _structure(path,namespace):
    """Stable roles and multiplicities. Actual IDs remain checked within a run."""
    documents={}
    for key in ('nodes','edges'):
        file=Path(path)/('api-'+key+'.json')
        if not file.is_file():
            return None
        documents[key]=read(file).get('data',{}).get(key,[])
    nodes={n['id']:n for n in documents['nodes'] if n.get('namespace')==namespace}
    # Workload and Pod generations have generated names. Follow their recorded
    # owner chain to the stable workload name instead of stripping suffixes.
    parent={e['target']:e['source'] for e in documents['edges']
            if e['relation']=='contains' and e['source'] in nodes and e['target'] in nodes
            and nodes[e['source']]['kind'] in ('workload','pod')}
    def role(id):
        node=nodes[id]
        root=id;seen=set()
        while root in parent and root not in seen:
            seen.add(root);root=parent[root]
        name=node.get('name') if root==id or node['kind']=='container' else None
        return node['kind'],nodes[root].get('name'),name
    structural=Counter(role(id) for id in nodes)
    edges=Counter((e['relation'],role(e['source']),role(e['target']))
                  for e in documents['edges'] if e['relation'] not in ('calls','runs_on')
                  and e['source'] in nodes and e['target'] in nodes)
    slot_by_node={}
    versions=Path(path)/'versions-after.json'
    if versions.is_file():
        slot_by_node={n['name']:n['slot'] for n in read(versions).get('nodes',[]) if 'slot' in n}
    all_nodes={n['id']:n for n in documents['nodes']}
    placements=Counter((role(e['source']),slot_by_node.get(all_nodes.get(e['target'],{}).get('name')))
                       for e in documents['edges'] if e['relation']=='runs_on' and e['source'] in nodes)
    return (structural,edges,placements) if structural and edges and placements and all(k[1] for k in placements) else None


def compare_pairs(matrix,exported,thresholds=None):
    thresholds={**DEFAULTS,**(thresholds or {})}
    baselines={(s['repeat'],s['cell']):exported.get(s['id']) for s in matrix if s['phase']=='baseline'}
    output=[]
    pairs=[('source-independence-'+demo,(1,demo+'-trace'),(1,demo+'-mesh'))
           for demo in ('boutique','astronomy')]
    pairs.extend(('clean-rebuild-boutique-'+source,(1,'boutique-'+source),(2,'boutique-'+source))
                 for source in ('trace','mesh'))
    for claim,first,second in pairs:
        records=[baselines.get(key) for key in (first,second)]
        evidence=[row[0] for row in records if row and row[0]]
        details={}
        if all(records):
            left,right=[row[1] for row in records]
            populations=[_observation(data) for data in (left,right)]
            details['populations']=populations
            if all(populations):
                relative=thresholds['request_relative_tolerance']
                absolute=thresholds['request_absolute_tolerance']
                a,b=populations
                accounting=[compare(row['reference_calls'],row['settled_calls'],relative,absolute)
                            for row in populations]
                equivalent=compare(a['ratio'],b['ratio'],relative,absolute/min(a['reference_calls'],b['reference_calls']))
                details['accounting']=dict(per_run=accounting,between_runs=equivalent,
                    state=_verdict([equivalent['state'],*[x['state'] for x in accounting]]))
            if claim.startswith('source-independence'):
                common=[]
                for data in (left,right):
                    scope=data.get('manifest',{}).get('namespace')
                    path=Path(data['path'])/'consumer/ledger.json'
                    ledger=read(path) if path.is_file() else {}
                    graph=ledger.get('after',{}) if ledger.get('openapi_validated') is True else {}
                    names={'frontend','checkout' if data.get('spec',{}).get('demo')=='astronomy' else 'checkoutservice'}
                    candidates=[n for n in graph.get('nodes',[]) if n.get('kind')=='service'
                                and n.get('namespace')==scope and n.get('name') in names]
                    chosen={n['name']:n['id'] for n in candidates}
                    common.append(chosen if scope and len(candidates)==len(names) and set(chosen)==names
                                  and all(chosen.values()) else None)
                details['logical_identity']={'state':'pass' if all(common) and common[0]==common[1]
                                            else 'fail' if all(common) else 'insufficient','observed':common}
            else:
                structures=[_structure(data['path'],data.get('manifest',{}).get('namespace')) for data in (left,right)]
                details['structure']={'state':'pass' if all(structures) and structures[0]==structures[1] else
                                      'fail' if all(structures) else 'insufficient'}
                question_ids=('interval-callers','current-replicas-and-placement','container-resource-history',
                              'request-error-derivation','request-error-numerical-agreement')
                answers={key:[data.get('thesis_answers',{}).get(key,{}).get('state') for data in (left,right)]
                         for key in question_ids}
                details['independent_api_answers']={'state':_verdict([
                    'fail' if 'incorrect' in states else 'pass' if states==['correct','correct'] else 'insufficient'
                    for states in answers.values()]),'observed':answers}
                sources=[data.get('source_selection',{}).get('expected_source') for data in (left,right)]
                details['source_reproducibility']={'state':'pass' if all(sources) and sources[0]==sources[1]
                                                   else 'fail' if all(sources) else 'insufficient'}
        states=[detail.get('state') for detail in details.values() if isinstance(detail,dict)]
        if 'accounting' not in details:
            states.append('insufficient')
        output.append(dict(claim=claim,verdict=_verdict(states),artifacts=evidence,
                           reference='independent checkout records and sealed canonical observations',
                           details=details))
    return output
