"""Revision-specific inputs and claim-level evidence for the thesis schedule."""
from __future__ import annotations
from .models import RECOVERABLE_SCHEDULES

import math
import re
from pathlib import Path

from .models import THESIS,THESIS_SCHEDULES,REPAIR,REPAIR_SCHEDULES,sha

START_REVISION='f37f408dea46abfcfdfbdc6a32c06ac2a353d9ef'
PRODUCT_NAMES=('karthos-backend','karthos-frontend')
LOCAL_CHECKS=('structure','source-semantics','arbitration','temporal-history','api-only')


def local_contract(receipt,pins,*,directory=None):
    """Validate unchanged local product checks without rewriting their receipt.

    Thesis v3 changes the GKE traffic schedule, not these five local fixture
    contracts. A v2 receipt can qualify the same exact product revision and
    source hash; its protocol, artifact references and hashes remain original.
    """
    checks=receipt.get('checks',{}) if isinstance(receipt,dict) else {}
    if (set(checks)!=set(LOCAL_CHECKS) or receipt.get('protocol') not in THESIS_SCHEDULES
            or receipt.get('revision')!=pins['karthos_revision']
            or receipt.get('source_sha256')!=pins['karthos_source_hash']
            or any(row.get('state')!='pass' or not row.get('reference')
                   or not re.fullmatch('[a-f0-9]{64}',row.get('sha256',''))
                   for row in checks.values())):
        raise ValueError('local thesis contract qualification is incomplete for the pinned product')
    if directory is not None:
        directory=Path(directory).resolve()
        for row in checks.values():
            reference=Path(row['reference'])
            path=directory/reference
            if len(reference.parts)!=1 or path.is_symlink() or not path.is_file() or sha(path)!=row['sha256']:
                raise ValueError('local contract artifact missing or checksum mismatch: '+str(reference))
    return receipt


def product_inputs(snapshot, lock, historical, image_lock):
    """Pin product files separately from the historical workloads and thresholds."""
    from .bundle import _product
    from ..runner import tree_digest
    pins=dict(historical)
    keys=('karthos_revision','karthos_source_hash','karthos_chart_hash')
    if lock.get('schema_version')!=1 or not all(re.fullmatch('[a-f0-9]{40}' if k=='karthos_revision' else '[a-f0-9]{64}',lock.get(k,'')) for k in keys):
        raise ValueError('explicit clean revision and product/chart SHA-256 pins required')
    pins.update({key:lock[key] for key in keys},karthos_dirty=False)
    files,missing,revision,pending=_product(snapshot,pins)
    if missing or pending or not files or revision!=pins['karthos_revision']:
        raise ValueError('thesis product snapshot has incomplete source closure')
    if sha(snapshot/'docs/openapi.yaml')!=lock.get('openapi_sha256'):
        raise ValueError('OpenAPI contract differs from the reviewed product lock')
    pins['openapi_sha256']=lock['openapi_sha256']
    chart=Path(lock['chart_path'])
    if tree_digest(chart,exclude={'.git'})!=pins['karthos_chart_hash']:
        raise ValueError('thesis chart differs from the reviewed product lock')
    original=snapshot/'deploy/helm/karthos'
    chart_files={str(p.relative_to(original)):sha(p) for p in original.rglob('*')
                 if p.is_file() and 'charts' not in p.relative_to(original).parts}
    if chart_files!={str(p.relative_to(chart)):sha(p) for p in chart.rglob('*')
                    if p.is_file() and 'charts' not in p.relative_to(chart).parts}:
        raise ValueError('packaged chart does not match the pinned product chart')
    images=lock.get('images',{})
    if not set(images)<=set(PRODUCT_NAMES):
        raise ValueError('unexpected thesis product image')
    result={key:value for key,value in image_lock.items()
            if not any('/'+name+':' in key or key.startswith(name+':') for name in PRODUCT_NAMES)}
    for name in PRODUCT_NAMES:
        image=images.get(name,'')
        if not re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}',image):
            pending.append(name+' needs a published immutable thesis image')
        else:
            result[name+':latest']=image
    return pins,result,chart,pending


def openapi_projection(path,expected_sha):
    from ..yamlio import load_mapping
    if sha(path)!=expected_sha:
        raise ValueError('consumer OpenAPI checksum differs from the pinned product')
    api=load_mapping(path.read_text())
    routes=('/graph/nodes','/graph/edges','/metrics')
    return {'schemas':api['components']['schemas'],'responses':{
        route:api['paths']['/api/v1'+route]['get']['responses']['200']['content']['application/json']['schema']
        for route in routes}}


def replica_inventory(ctx,namespace,service):
    """Independent Kubernetes selection. Keep names, node assignments and UID."""
    svc=ctx.json('get','service',service,'-n',namespace)
    selector=svc.get('spec',{}).get('selector') or {}
    if not selector:
        raise ValueError('checkout Service has no independent Pod selector')
    selector=','.join(f'{key}={value}' for key,value in sorted(selector.items()))
    items=ctx.json('get','pods','-n',namespace,'-l',selector).get('items',[])
    def health(status):
        phase=status.get('phase')
        if phase=='Running':
            ready=[c.get('status') for c in status.get('conditions',[]) if c.get('type')=='Ready']
            return {'True':'healthy','False':'running_not_ready'}.get(ready[0],'unknown') if len(ready)==1 else 'unknown'
        return {'Pending':'pending','Succeeded':'succeeded','Failed':'failed'}.get(phase,'unknown')
    return sorted(({'pod':p['metadata']['name'],'uid':p['metadata']['uid'],
                    'node':p.get('spec',{}).get('nodeName'),
                    'health':health(p.get('status',{}))}
                   for p in items if not p['metadata'].get('deletionTimestamp')),
                  key=lambda row:row['pod'])


def _verdict(expected,observed,*,reference):
    if expected is None or observed is None:
        return dict(state='insufficient',expected=expected,observed=observed,reference=reference)
    return dict(state='correct' if expected==observed else 'incorrect',
                expected=expected,observed=observed,reference=reference)


def _answer(doc,key):
    if doc.get('openapi_validated') is not True:
        return None
    answer=doc.get('answers',{}).get(key,{})
    return answer.get('value') if answer.get('state')=='observed' else None


def score_history(document, before, after):
    def projected(records):
        if not records or any(not row.get('node') or row.get('health')=='unknown' for row in records):
            return None
        return [{key:row[key] for key in ('pod','node','health')} for row in records]
    earlier,later=projected(before),projected(after)
    old,new=_answer(document,'before_replicas'),_answer(document,'after_replicas')
    expected=[earlier,later] if earlier is not None and later is not None else None
    observed=[old,new] if (old is not None and new is not None and document.get('snapshot_changed') is True
                          and all(row.get('health') not in (None,'unknown') for row in [*old,*new])) else None
    return _verdict(expected,observed,reference='UID-scoped Kubernetes inventory before and after the replacement')


def _caller_scores(document, report):
    from ..consumers.api_only import graph_calls_source, observation_intersects, scoped_service
    from ..scoring import stamp
    from ..timing_analysis import linked_checkout_traces, scoped_mesh_checkout_partitions

    manifest=report.get('manifest',{})
    namespace,source=manifest.get('namespace'),manifest.get('source_id')
    service={'boutique':'checkoutservice','astronomy':'checkout'}.get(report.get('spec',{}).get('demo'))
    selection=report.get('source_selection',{})
    graph=document.get('after',{})
    target=scoped_service(graph,namespace,service)
    frontend=scoped_service(graph,namespace,'frontend')
    scope=document.get('scope',{})
    graph_source=graph_calls_source(source)
    expected=None;observed=None
    traffic=report.get('traffic',{}).get('report') or {}
    whole=traffic.get('windows',{}).get('whole',{})
    observers=whole.get('observers',{})
    try:
        bounds=[stamp(manifest[k]) for k in ('start','end')]
        same_bounds=bounds==[stamp(scope[k]) for k in ('from','to')]
    except (KeyError,TypeError,ValueError):
        same_bounds=False
    scoped=(same_bounds and namespace and service and graph_source and target and frontend
            and graph.get('state')=='observed'
            and scope.get('namespace')==namespace and scope.get('service')==service
            and scope.get('target_id')==target and scope.get('declared_caller_id')==frontend
            and scope.get('metric_source')==source and scope.get('calls_source')==graph_source
            and selection.get('state')=='pass' and selection.get('expected_source')==source)
    calls=_answer(document,'interval-callers')
    if scoped and traffic.get('recorder_identity_valid') is True and all(
            row.get('completion',{}).get('requests',0)>0
            for row in (observers.get('frontend',{}),whole.get('checkout_place_order',{}))):
        if source=='traces.servicegraph':
            linked=set(linked_checkout_traces(traffic) or [])
            corroborated=any(row.get('trace_id') in linked and row.get('target_service')==service
                and row.get('paired_frontend') and not row.get('conflicting_identity')
                and bounds[0]<=row['completion']<bounds[1]
                for row in traffic.get('events',{}).get('traces',[]))
        else:
            corroborated=any((row.get('increase') or 0)>0 and not row.get('resets')
                              for row in scoped_mesh_checkout_partitions(whole,namespace,service))
        if corroborated:
            expected=[{'caller':'frontend','caller_id':frontend}]
    if scoped and calls is not None:
        candidates={node['id']:node['name'] for node in graph['nodes']
                    if node.get('kind')=='service' and node.get('namespace')==namespace}
        observed=[]
        for row in calls:
            caller=row.get('caller_id')
            valid=(caller in candidates and candidates[caller]==row.get('caller')
                   and row.get('target_id')==target and row.get('namespace')==namespace
                   and row.get('provenance')==[graph_source]
                   and observation_intersects(row,scope['from'],scope['to']))
            value={'caller':row.get('caller'),'caller_id':caller}
            if not valid:
                value['invalid_identity_or_observation']=True
            if value not in observed:
                observed.append(value)
        observed.sort(key=lambda row:(str(row['caller']),str(row['caller_id'])))
    return _verdict(expected,observed,
        reference='declared frontend Service to checkout Service in the selected graph source and independent observer interval')


def _error_scores(document,report):
    from ..consumers.api_only import scoped_service
    from ..scoring import DEFAULTS, compare, derivation_contract, stamp
    from ..series_identity import partition_key

    errors=_answer(document,'request-error-derivation')
    samples=errors.get('samples',[]) if isinstance(errors,dict) else []
    manifest=report.get('manifest',{})
    namespace,source=manifest.get('namespace'),manifest.get('source_id')
    service={'boutique':'checkoutservice','astronomy':'checkout'}.get(report.get('spec',{}).get('demo'))
    graph=document.get('after',{})
    caller,target=scoped_service(graph,namespace,'frontend'),scoped_service(graph,namespace,service)
    selection=report.get('source_selection',{})
    scoped=bool(namespace and service and caller and target and selection.get('state')=='pass'
                and selection.get('expected_source')==source and graph.get('state')=='observed')

    def same_edge(labels):
        return (labels.get('source'),labels.get('edge_client'),labels.get('edge_server'),
                labels.get('edge_client_id'),labels.get('edge_server_id'))==(source,'frontend',service,caller,target)

    def same_series(sample):
        return (same_edge(sample.get('labels',{})) and sample.get('entity_id')==caller
                and sample.get('metric_name')=='servicegraph.request.error.count')

    metadata_ok=bool(samples and all(same_series(row) for row in samples)
                     and derivation_contract(samples,source)['state']=='pass')
    provenance=_verdict(True,metadata_ok if scoped and samples else None,
                        reference='selected source, canonical edge and published error derivation policy')
    storage_ref='exact VictoriaMetrics query for the same full series and evaluation instant'
    independent_ref='aligned independent fixed-window checkout source errors under the selected source policy'
    storage=_verdict(True,None,reference=storage_ref)
    independent=_verdict(True,None,reference=independent_ref)
    traffic=report.get('traffic',{}).get('report') or {}
    whole=traffic.get('windows',{}).get('whole',{})
    query=errors.get('query',{}) if isinstance(errors,dict) else {}
    try:
        start,end=(stamp(manifest[k]) for k in ('start','end'))
        # Go renders whole-minute durations as 1m0s, 5m0s, and so on.
        window=re.fullmatch(r'(?:(\d+)m)?(?:(\d+)s)?',query.get('window',''))
        duration=sum(int(part or 0)*scale for part,scale in zip(window.groups(),(60,1))) if window else 0
        fixed=(query.get('aggregation')=='increase' and query.get('source')==source
               and stamp(query['from'])==stamp(query['to'])==end and duration==end-start>0)
    except (KeyError,TypeError,ValueError,OverflowError):
        fixed=False
    if scoped and samples and fixed and all(same_series(row) for row in samples):
        parity=whole.get('api_storage_parity',{}).get('checks',{})
        checks=parity.get('exact_error_count_api',parity.get('error-count',{})).get('checks',[])
        measured={};reference={};conflicts=False
        def value(number):
            return type(number) in (int,float) and math.isfinite(number)
        for sample in samples:
            if stamp(sample['timestamp'])!=end or sample.get('unit')!='count' or not value(sample.get('value')):
                conflicts=True;continue
            partition=partition_key(sample['labels'])
            if partition in measured:
                conflicts=True
            measured[partition]=sample['value']
        for row in checks:
            labels=row.get('labels',{})
            if row.get('timestamp')==end and same_edge(labels) and value(row.get('reference')):
                partition=partition_key(labels)
                if partition in reference:
                    conflicts=True
                reference[partition]=row['reference']
        if measured and reference and not conflicts:
            same_values=(measured.keys()==reference.keys() and all(
                         math.isclose(measured[k],reference[k],rel_tol=0,abs_tol=1e-9) for k in measured))
            storage=dict(_verdict(True,same_values,reference=storage_ref),expected_partitions=reference,
                         observed_partitions=measured,evaluation_timestamp=end)
        population=traffic.get('fixed_window_error_reference') or {}
        expected=population.get('errors')
        if (measured and not conflicts and value(expected) and population.get('state')=='available'
                and population.get('source_id')==source and population.get('namespace')==namespace
                and population.get('caller')=='frontend' and population.get('target')==service
                and population.get('bounds_seconds')==[start,end]):
            thresholds={**DEFAULTS,**report.get('thresholds',{})}
            numeric=compare(expected,sum(measured.values()),thresholds['request_relative_tolerance'],
                            thresholds['request_absolute_tolerance'])
            independent=dict(_verdict(True,numeric['state']=='pass',reference=independent_ref),
                             comparison=numeric,population=population)
    states=(storage['state'],independent['state'])
    state='incorrect' if 'incorrect' in states else 'correct' if states==('correct','correct') else 'insufficient'
    return provenance,dict(state=state,reference=independent_ref,storage_api_parity=storage,
                           independent_agreement=independent)


def score_window_answers(document, before, after, report):
    new=_answer(document,'after_replicas')
    placements=[{k:row[k] for k in ('pod','node')} for row in new] if new is not None else None
    inventory=[{k:row[k] for k in ('pod','node')} for row in before] if before and before==after else None
    answers={'current-replicas-and-placement':_verdict(inventory,placements,
             reference='stable independent Kubernetes inventory around the consumer')}
    answers['interval-callers']=_caller_scores(document,report)
    resource_answer=document.get('answers',{}).get('container-resource-history',{}) if document.get('openapi_validated') else {}
    checks=[row for row in report.get('resources',{}).get('checks',[])
            if row.get('container')==resource_answer.get('container')]
    from ..evidence_v4 import vm_time_identity
    resource=[]
    for response in resource_answer.get('value',[]):
        for sample in response.get('samples',[]):
            name=sample.get('metric_name')
            kind={'container.cpu.usage':'cpu','container.memory.usage':'memory'}.get(name)
            at=vm_time_identity(sample['timestamp'])['timestamp_ms']
            candidates=[row for row in checks if row.get('metric')==kind
                        and row.get('evaluation_timestamp_ms')==at
                        and row.get('preservation',{}).get('api') is not None]
            detail={'metric':kind,'evaluation_timestamp_ms':at,'value':sample['value'],'state':'insufficient'}
            if len(candidates)==1:
                row=candidates[0]
                agreement=row.get('independent_agreement',{})
                api_value=row['preservation']['api']
                agrees=math.isclose(api_value,sample['value'],rel_tol=1e-12,abs_tol=1e-8)
                detail.update(state=row.get('state','insufficient') if agrees else 'fail',
                              reference=agreement.get('reference'),compared_api_value=api_value,
                              measurement_timestamp_ns=row.get('measurement_timestamp_ns'))
            resource.append(detail)
    measurements={kind:{row['measurement_timestamp_ns'] for row in resource if row['metric']==kind
                        and row.get('measurement_timestamp_ns') is not None and row['state']=='pass'}
                  for kind in ('cpu','memory')}
    complete=all(len(observed)>=2 for observed in measurements.values()) and all(row['state']=='pass' for row in resource)
    answers['container-resource-history']=dict(_verdict(True,
        False if any(row['state']=='fail' for row in resource) else True if complete else None,
        reference='same underlying kubelet measurements, full container identity and evaluation times'),measurements=resource)
    provenance,numerical=_error_scores(document,report)
    answers['request-error-derivation']=provenance
    answers['request-error-numerical-agreement']=numerical
    answers['retained-replicas-and-health']={'state':'insufficient','reference':'dedicated checkout replacement evidence'}
    return answers


def claim_matrix(ctx,windows,history,readiness_report):
    """Link independently scored claims to the exact sealed sources."""
    from .report_summary import load_summary
    by_window={}
    for row in windows:
        path=Path(row['path'])/'gke-evaluation.json' if row.get('path') else None
        if (path and (path.is_file() or (path.parent/'orchestration-summary.json').is_file()) and row.get('export',{}).get('verified') is True
                and row.get('evidence_valid') is True and row.get('execution')=='complete'):
            evidence=load_summary(path.parent)
            if evidence.get('evidence',{}).get('valid') is True and evidence.get('execution',{}).get('state')=='complete':
                by_window[row['id']]=(row['export'].get('uri'),evidence)
    rows=[]
    required_baselines=sum(row['phase']=='baseline' for row in ctx.manifest['matrix']) if ctx.manifest.get('schedule') in REPAIR_SCHEDULES else 6
    def add(claim,verdict,reference,artifacts):
        rows.append(dict(claim=claim,verdict=verdict,reference=reference,artifacts=artifacts))
    local=(ctx.manifest.get('local_contract_evidence') or {}).get('checks',{})
    for key in LOCAL_CHECKS:
        check=local.get(key,{})
        add('local-'+key,check.get('state','insufficient'),check.get('reference'),
            [check['sha256']] if check.get('sha256') else [])
    ready=(readiness_report or {}).get('matrix_export',{})
    readiness_reference='verified current-launch probes, ownership-safe reset and timing handoff'
    readiness_artifacts=[ready['uri']] if ready.get('uri') else []
    if ctx.manifest.get('schedule') in RECOVERABLE_SCHEDULES:
        readiness_reference='read-only per-cell deployment, source configuration and public API observations; no readiness traffic or numerical scoring'
        readiness_artifacts=[row['export']['uri'] for row in (readiness_report or {}).get('cells',[])
                             if row.get('export',{}).get('verified') is True and row['export'].get('uri')]
    add('gke-readiness','pass' if (readiness_report or {}).get('ready') is True else 'insufficient',
        readiness_reference,readiness_artifacts)
    for name in ('interval-callers','current-replicas-and-placement','container-resource-history','request-error-derivation','request-error-numerical-agreement'):
        entries=[(uri,data.get('thesis_answers',{}).get(name,{}).get('state')) for uri,data in by_window.values()
                 if data.get('spec',{}).get('phase')=='baseline']
        states=[state for _,state in entries]
        verdict='fail' if 'incorrect' in states else 'pass' if required_baselines>0 and len(states)==required_baselines and all(s=='correct' for s in states) else 'insufficient'
        add(name,verdict,'API-only answers scored against independent evidence',[uri for uri,_ in entries if uri])
    history=history or {}
    history_state=history.get('answer',{}).get('state') if (
        history.get('export',{}).get('verified') is True and history.get('continuation_safe') is True) else None
    add('retained-replicas-and-health',{'correct':'pass','incorrect':'fail'}.get(history_state,'insufficient'),
        'bounded GKE checkout replacement with independently observed before and after inventories',
        [history['export']['uri']] if history.get('export',{}).get('verified') else [])
    baselines=[(uri,data) for uri,data in by_window.values() if data.get('spec',{}).get('phase')=='baseline']
    for claim,reference,extract in (
        ('catalog-contract', '43 pinned names, units, sources and aggregation contracts',
         lambda d: 'pass' if (d.get('coverage',{}).get('contract') or {}).get('valid') else 'insufficient'),
        ('representative-numerical-agreement', 'aligned source and independent measurements; exact API/storage checks reported separately',
         lambda d: d.get('numerical_agreement',{}).get('state')),
    ):
        states=[extract(data) for _,data in baselines]
        verdict='fail' if 'fail' in states else 'pass' if required_baselines>0 and len(states)==required_baselines and all(state=='pass' for state in states) else 'insufficient'
        add(claim,verdict,reference,[uri for uri,_ in baselines if uri])
    if ctx.manifest.get('schedule') in REPAIR_SCHEDULES:
        for row in rows:
            if row['claim']=='retained-replicas-and-health':
                row.update(verdict='not-applicable',reference='deferred: no new cloud historical observation in repair validation',artifacts=[])
    from .thesis_comparison import compare_pairs
    comparisons=compare_pairs(ctx.manifest['matrix'],by_window,ctx.manifest.get('thresholds'))
    if ctx.manifest.get('schedule') in REPAIR_SCHEDULES:
        for row in comparisons:
            if row['claim']=='source-independence-astronomy':
                row.update(verdict='not-applicable',reference='deferred: repair includes Astronomy Istio only; cross-source equivalence requires a fresh Astronomy trace baseline')
            if row['claim'].startswith('clean-rebuild') or row['claim'].startswith('reproducibility'):
                row.update(verdict='not-applicable',reference='deferred: one fresh repaired cluster cannot establish two-cluster reproducibility',artifacts=[])
    rows.extend(comparisons)
    return dict(protocol=ctx.manifest.get('schedule',THESIS),
                local_contract_protocol=(ctx.manifest.get('local_contract_evidence') or {}).get('protocol'),
                product_revision=ctx.manifest['product_pins']['karthos_revision'],claims=rows)
