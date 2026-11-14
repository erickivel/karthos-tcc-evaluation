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


def representative_state(data):
    """Selected mechanisms, with a separately evidenced settled duration view.

    An insufficient fixed-window duration population can use the explicitly
    matched settled population only with covered evidence and exact API/storage
    parity. Neither this alternative nor bounded claims erase numerical failures.
    """
    families=data.get('families',{})
    required=('traffic_count','traffic_error','traffic_duration','container_resource_accuracy','volume_accuracy')
    states={key:families.get(key,{}).get('state','insufficient') for key in required}
    if 'fail' in states.values():return 'fail'
    if (states['traffic_duration']!='pass'
            and all(families.get(key,{}).get('state')=='pass' for key in
                    ('evidence_horizon','exact_api_storage_parity'))):
        states['traffic_duration']=families.get('settled_traffic_duration',{}).get('state','insufficient')
    return _verdict(list(states.values()))


def arbitration_claim(matrix,exported):
    """Both availability/selection and explicit transition need captured evidence."""
    expected={'both-sources':'mesh.istio','replanned-trace':'traces.servicegraph'}
    stages={}
    artifacts=[]
    for spec in matrix:
        scenario=spec.get('scenario')
        if spec.get('phase')!='arbitration' or scenario not in expected:continue
        row=exported.get(spec['id'])
        if not row:continue
        uri,data=row;artifacts.append(uri)
        selection=data.get('source_selection',{})
        state=selection.get('state','insufficient')
        if selection.get('expected_source')!=expected[scenario]:state='fail' if state=='pass' else 'insufficient'
        if scenario=='replanned-trace':
            transition=data.get('arbitration_transition')
            path=Path(data.get('path',''))/'setup-arbitration-transition.json'
            if transition is None and path.is_file():transition=read(path)
            if not (isinstance(transition,dict) and transition.get('mode')=='rediscover-redeploy'
                    and transition.get('runtime_failover_claim') is False
                    and [event.get('step') for event in transition.get('events',[])]==['istio-unavailable','rediscovered','redeployed']):
                state='insufficient'
        stages[scenario]=state
    return {'claim':'source-arbitration','verdict':_verdict([stages.get(key,'insufficient') for key in expected]),
            'reference':'captured both-available selection followed by explicit rediscovery and redeployment; no runtime failover claim',
            'artifacts':[uri for uri in artifacts if uri],'stages':stages}


def compare_deployments(left_manifest,right_manifest,*,left_sha256,right_sha256):
    """Compare separately sealed baseline inputs without changing either run.

    Each manifest binds run_id, distinct cluster_identity, product_pins,
    thresholds and baselines. Each baseline binds cell, directory (relative to
    the manifest), and exact hashes of its bounded summary and optional graph,
    versions and consumer ledger files. Supply manifest hashes from the sealed
    evidence index, rather than computing trust from an unverified download.
    """
    import re
    from .models import sha
    allowed={'orchestration-summary.json','gke-evaluation.json','api-nodes.json','api-edges.json',
             'versions-after.json','consumer/ledger.json'}
    def load_manifest(path,expected):
        path=Path(path).resolve()
        if sha(path)!=expected:raise ValueError('cross-deployment manifest checksum mismatch')
        document=read(path)
        if not document.get('run_id') or not document.get('cluster_identity'):
            raise ValueError('run and cluster identities required')
        pins=document.get('product_pins',{})
        if not (re.fullmatch('[a-f0-9]{40}',str(pins.get('karthos_revision','')))
                and re.fullmatch('[a-f0-9]{64}',str(pins.get('karthos_source_hash','')))):
            raise ValueError('exact product revision and source fingerprint required')
        if not isinstance(document.get('thresholds'),dict):
            raise ValueError('explicit frozen scoring thresholds required')
        result={}
        for item in document.get('baselines',[]):
            cell=item.get('cell')
            if cell not in ('boutique-trace','boutique-mesh') or cell in result:
                raise ValueError('one Boutique baseline per source required')
            directory=(path.parent/item['directory']).resolve()
            if not directory.is_relative_to(path.parent):raise ValueError('baseline escapes comparison bundle')
            files=item.get('files',{})
            if set(files)-allowed:raise ValueError('unexpected comparison member')
            if not ({'orchestration-summary.json','gke-evaluation.json'}&set(files)):
                raise ValueError('bounded baseline report required')
            for name in allowed:
                file=directory/name
                if file.exists() and name not in files:raise ValueError('unbound comparison member: '+name)
                if name in files and (file.is_symlink() or not file.resolve().is_relative_to(directory) or not file.is_file() or sha(file)!=files[name]):
                    raise ValueError('comparison member checksum mismatch: '+name)
            from .report_summary import load_summary
            report=load_summary(directory)
            if report.get('state')=='unavailable':raise ValueError('bounded comparison summary unavailable')
            report['path']=str(directory)
            spec=report.get('spec',{})
            if spec.get('phase')!='baseline' or spec.get('cell')!=cell:
                raise ValueError('comparison report differs from declared cell/baseline')
            result[cell]=(str(path)+'#'+cell,report)
        return document,result
    first,left=load_manifest(left_manifest,left_sha256)
    second,right=load_manifest(right_manifest,right_sha256)
    if first['product_pins']!=second['product_pins']:raise ValueError('product pins differ across deployments')
    if first.get('thresholds')!=second.get('thresholds'):raise ValueError('scoring thresholds differ across deployments')
    if first['cluster_identity']==second['cluster_identity'] or first['run_id']==second['run_id']:
        raise ValueError('distinct run and cluster identities required')
    matrix=[];exported={}
    for repeat,records in ((1,left),(2,right)):
        for cell,record in records.items():
            key=str(repeat)+'-'+cell
            matrix.append({'id':key,'repeat':repeat,'cell':cell,'phase':'baseline'})
            exported[key]=record
    for cell in set(left)&set(right):
        specs=[records[cell][1]['spec'] for records in (left,right)]
        for key in ('generator','rate','duration_seconds','scenario'):
            if any(spec.get(key) is None for spec in specs) or specs[0][key]!=specs[1][key]:
                raise ValueError('workload differs or is unavailable: '+key)
    claims=[row for row in compare_pairs(matrix,exported,first.get('thresholds')) if row['claim'].startswith('clean-rebuild')]
    return {'version':'cross-deployment-v1','runs':[first['run_id'],second['run_id']],
            'input_sha256':[left_sha256,right_sha256],'historical_passes_imported':False,
            'claims':claims,'scope':'common independently validated baseline populations; newly captured evidence stays separate'}
