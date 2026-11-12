"""Evidence checks for discovery-time selection, never runtime failover."""
import time


def assert_selection(profile, plan, samples, expected_source):
    """Check captured profile/source plan/mounted config and public samples.

    plan bundles source_plan, backend_source_contract (capabilityProfile,
    sourcePlan), and parsed collector_configs. Missing evidence is insufficient;
    contradictory captured evidence fails. No metric conversion occurs here.
    """
    if expected_source not in ('mesh.istio','traces.servicegraph'):
        raise ValueError('expected traffic source must be mesh.istio or traces.servicegraph')
    missing=[];errors=[]
    resolved=plan.get('source_plan') or {}
    mounted=plan.get('backend_source_contract') or {}
    configs=plan.get('collector_configs')
    if not profile or not resolved or not mounted or configs is None:
        return {'state':'insufficient','reasons':['profile, source plan, mounted backend contract and collector configs required']}
    if mounted.get('capabilityProfile')!=profile or mounted.get('sourcePlan')!=resolved:
        errors.append('mounted backend source contract does not own captured profile/source plan')
    if not resolved.get('profile_fingerprint'): missing.append('captured profile fingerprint absent')
    caps=profile.get('capabilities') or []
    matching=[c for c in caps if c.get('source_id')==expected_source]
    if len(matching)!=1 or matching[0].get('status')!='available':
        errors.append('selected source lacks one available capability')
    selected=resolved.get('selected_sources') or []
    rival='traces.servicegraph' if expected_source=='mesh.istio' else 'mesh.istio'
    if selected.count(expected_source)!=1 or rival in selected:
        errors.append('selected source ownership is absent/duplicate or competing source selected')
    assignments=resolved.get('metric_assignments') or {}
    shared={name:source for name,source in assignments.items() if name in {
        'servicegraph.request.count','servicegraph.request.error.count','servicegraph.request.duration'}}
    if not shared: missing.append('shared traffic metric assignments absent')
    if any(source!=expected_source for source in shared.values()): errors.append('shared traffic assignment differs from expected source')
    connectors=[];active=[]
    for config in configs:
        for name in (config.get('connectors') or {}):
            if name.split('/',1)[0] in ('service_graph', 'servicegraph'):
                connectors.append(name)
                pipelines=(config.get('service') or {}).get('pipelines') or {}
                exported=any(name in p.get('exporters',[]) for k,p in pipelines.items() if k.split('/')[0]=='traces')
                received=any(name in p.get('receivers',[]) for k,p in pipelines.items() if k.split('/')[0]=='metrics')
                if exported and received: active.append(name)
    if expected_source=='mesh.istio' and connectors:
        errors.append('Istio selection retains duplicate derivative servicegraph connector')
    if expected_source=='traces.servicegraph' and (len(connectors)!=1 or len(active)!=1):
        errors.append('trace selection requires exactly one configured and active servicegraph connector')
    traffic=[s for s in samples if s.get('metric_name') in shared]
    if not traffic: missing.append('no observed shared traffic samples')
    for sample in traffic:
        source=(sample.get('labels') or {}).get('source')
        if source is None: missing.append('traffic sample source provenance absent')
        elif source!=expected_source: errors.append('observed sample is owned by a different source')
    return {'state':'fail' if errors else 'insufficient' if missing else 'pass',
            'expected_source':expected_source,'errors':errors,'missing':missing,
            'sample_count':len(traffic),'servicegraph_connectors':connectors,
            'ownership_check':'captured mounted profile/plan exact equality; fingerprint not independently recomputed'}


def transition_to_trace(*, make_istio_unavailable, rediscover, redeploy, clock=time.time):
    """Injected callbacks execute once in order; failures stop subsequent steps."""
    events=[]
    started=clock();make_istio_unavailable();events.append({'step':'istio-unavailable','completed_at':clock()})
    discovered=rediscover();events.append({'step':'rediscovered','completed_at':clock()})
    deployed=redeploy(discovered);events.append({'step':'redeployed','completed_at':clock()})
    return {'mode':'rediscover-redeploy','started_at':started,'events':events,
            'discovered':discovered,'deployed':deployed,'runtime_failover_claim':False}
