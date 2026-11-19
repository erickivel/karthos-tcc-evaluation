"""Bounded orchestration views over separately sealed detailed window evidence."""
from __future__ import annotations
import json
from pathlib import Path
from .models import read,write,sha

LIMIT=8*1024**2
SCOPE='orchestration summary; detailed evidence remains in sealed window archive'


def save_summary(path,report):
    path=Path(path);raw=path/'gke-evaluation.json'
    traffic=report.get('traffic',{}).get('report') or {}
    population=traffic.get('whole_capture_source_identity_coverage',{})
    generator=traffic.get('generator_contract',{})
    expected=population.get('checkout_trace_ids',[])
    # Only membership of expected checkout traces is used by linked_checkout_traces.
    paired=population.get('paired_trace_ids',[])
    expected_set={x for x in expected if isinstance(x,str)}
    calls=traffic.get('windows',{}).get('whole',{}).get('checkout_place_order',{})
    eventual=traffic.get('eventual_completeness',{})
    result={'schema_version':1,'report_scope':SCOPE,
            'source_report':{'name':raw.name,'sha256':sha(raw),'bytes':raw.stat().st_size},
            'path':report.get('path',str(path)),'spec':report.get('spec',{}),
            'execution':{'state':report.get('execution',{}).get('state')},
            'evidence':{'valid':report.get('evidence',{}).get('valid')},
            'manifest':{k:report.get('manifest',{}).get(k) for k in ('namespace','source_id')},
            'source_selection':report.get('source_selection',{}),
            'structural_fidelity':report.get('structural_fidelity',{}),
            'background_traffic':report.get('background_traffic',{}),
            'arbitration_transition':report.get('arbitration_transition') or (read(path/'setup-arbitration-transition.json') if (path/'setup-arbitration-transition.json').is_file() else None),
            'evidence_horizon':report.get('evidence_horizon',{}),
            **{key:report[key] for key in ('comparison_inputs','service_graph_evidence','boundary_summary','bounded_observations','analysis','raw_evidence_export','input_population','projected_input_proof','verifier_implementation_hashes') if key in report},
            'families':report.get('families') or family_summaries(report),
            'numerical_agreement':{'state':report.get('numerical_agreement',{}).get('state')},
            'coverage':{'contract':{'valid':(report.get('coverage',{}).get('contract') or {}).get('valid')}},
            'thesis_answers':{key:{'state':value.get('state')} for key,value in report.get('thesis_answers',{}).items()},
            'traffic':{'report':{
                'recorder_identity_valid':traffic.get('recorder_identity_valid'),
                'windows':{'whole':{'checkout_place_order':{'evidence_present':calls.get('evidence_present'),'completion':{'requests':calls.get('completion',{}).get('requests')}}}},
                'eventual_completeness':{k:eventual.get(k) for k in ('state','requests','numerical_differences')},
                'whole_capture_source_identity_coverage':{'checkout_trace_ids':expected,'paired_trace_ids':[x for x in paired if isinstance(x,str) and x in expected_set]},
                'generator_contract':{**{k:generator.get(k,[]) for k in ('checkout_request_ids','conflicting_request_ids')},'retained_generator':{k:v for k,v in (generator.get('retained_generator') or {}).items() if k in ('unexpected_outcomes','requests','completed','errors')}}}}}
    payload=json.dumps(result,indent=2,sort_keys=True)+'\n'
    if len(payload.encode())>LIMIT:raise ValueError('orchestration summary exceeds explicit size bound')
    write(path/'orchestration-summary.json',payload)
    if 'boundary_summary' in result:
        from .boundary_summary import render
        write(path/'boundary-summary.html',render(result['boundary_summary']))
    return result


def load_summary(path):
    """Never decode an unbounded detailed report during cumulative operations."""
    path=Path(path)
    candidate=path/'orchestration-summary.json'
    if not candidate.exists():candidate=path/'gke-evaluation.json'
    try:
        if candidate.is_symlink() or candidate.stat().st_size>LIMIT:
            raise ValueError('report exceeds orchestration read bound or is a symlink')
        value=read(candidate)
        if not isinstance(value,dict):raise ValueError('report must be an object')
        if candidate.name=='orchestration-summary.json' and (value.get('schema_version')!=1 or value.get('report_scope')!=SCOPE):
            raise ValueError('orchestration summary contract differs')
        return value
    except Exception as exc:
        return {'state':'unavailable','reason':str(exc),'path':str(path),'report_scope':'no bounded finalized summary available; raw evidence not decoded'}


def _family(checks,scope):
    states=[check.get('state','insufficient') for check in checks if isinstance(check,dict)]
    verdict='fail' if 'fail' in states else ('pass' if states and all(x=='pass' for x in states) else 'insufficient')
    return {'state':verdict,'scope':scope,'checks':len(states),
            'counts':{key:states.count(key) for key in ('pass','fail','insufficient','incomplete')}}


def family_summaries(report):
    """Bounded independent verdicts over existing checks; no new denominators."""
    traffic=report.get('traffic',{}).get('report') or {}
    selected={x.get('name'):x for x in report.get('selected_accuracy',[])}
    # Only versioned selected accuracy carries the source-population gate.
    families={key:_family([selected.get(metric,{})],scope) for key,metric,scope in (
        ('traffic_count','servicegraph.request.count','existing request-count parity and matched-source accuracy'),
        ('traffic_error','servicegraph.request.error.count','existing same-policy error-count parity and matched-source accuracy'),
        ('traffic_duration','servicegraph.request.duration','existing duration parity and matched-population accuracy'))}
    for key in ('traffic_count','traffic_error','traffic_duration'):
        families[key]['population_scope']='fixed-window'
    parity=traffic.get('api_storage_parity',{}).get('checks',{}) or traffic.get('windows',{}).get('whole',{}).get('api_storage_parity',{}).get('checks',{})
    aliases=(('exact_request_count_api','request-count'),('exact_error_count_api','error-count'),('exact_duration_api','duration'))
    families['exact_api_storage_parity']=_family([parity.get(a,parity.get(b,{})) for a,b in aliases],
        'whole fixed interval full-series request/error/duration parity; independent source accuracy separate')
    families['resource_accuracy']=_family([report.get('numerical_agreement',{})],'existing selected resource-only numerical scope')
    families['container_resource_accuracy']=_family([report.get('resources',{})],'application container CPU/memory conversion, preservation and independent agreement')
    resource_checks=report.get('numerical_agreement',{}).get('checks',[])
    for family,prefixes in (('host_resource_accuracy',('system.',)),('volume_accuracy',('k8s.pod.volume.','k8s.volume.')),('lifecycle_accuracy',('k8s.container.','k8s.pod.status.','k8s.pod.phase'))):
        families[family]=_family([check for check in resource_checks if str(check.get('metric',check.get('observed',{}).get('metric',''))).startswith(prefixes)],'selected '+family+' comparisons; absence stays insufficient')
    for family,metric in (('traffic_count_comparison','servicegraph.request.count'),('traffic_error_comparison','servicegraph.request.error.count'),('traffic_duration_comparison','servicegraph.request.duration')):
        families[family]=_family([check.get('independent_comparison',{}) for check in selected.get(metric,{}).get('checks',[])],'source/store numerical comparison under retained boundaries; no exact call-population claim')
    families['evidence_horizon']=_family([report.get('evidence_horizon',{})],'recorded evidence horizon and exact quiet endpoint coverage; fixed-window parity separate')
    settled=report.get('settled_duration_diagnostics',{})
    families['settled_traffic_duration']=dict(state='not-applicable',reason=settled.get('reason'),checks=0,counts={},scope='native settled histogram diagnostic not applicable to this source') if settled.get('state')=='not-applicable' else _family([settled.get('native_settled_to_declared_drain_export',{})],'matched settled histogram population inside recorded export horizon')
    families['fixed_window_traffic_duration']=dict(families['traffic_duration'],scope='original fixed-window duration accuracy; separate from settled population')
    if report.get('manifest',{}).get('source_id',traffic.get('manifest',{}).get('source_id'))=='mesh.istio':
        primary=dict(settled.get('native_settled_to_declared_drain_export',{}))
        if primary.get('state')=='pass' and report.get('evidence_horizon',{}).get('state')!='pass':
            primary.update(state='insufficient',reason='required export horizon evidence missing')
        families['traffic_duration']=_family([primary],'primary mesh duration: matched settled population and recorded export horizon; fixed window separate')
        families['traffic_duration']['population_scope']='settled'
        families['settled_traffic_duration']=dict(families['traffic_duration'])
    trace_settled=report.get('settled_trace_population')
    if trace_settled is not None and trace_settled.get('state')!='not-applicable':
        for key,metric in (('traffic_count','requests'),('traffic_error','errors'),('traffic_duration','duration')):
            families['fixed_window_'+key]=dict(families[key],scope='unchanged original fixed-window '+key+' diagnostic')
            item=dict(trace_settled.get(metric,{}))
            if trace_settled.get('state')=='insufficient':
                item.update(state='insufficient',reason=trace_settled.get('reason'))
            families[key]=_family([item],'primary trace settled aggregate '+metric+'; independent paired checkout population, raw source and stored series; exact API parity separate')
            families[key]['population_scope']='settled'
            families['settled_'+key]=dict(families[key])
    timing=traffic.get('required_evidence',{})
    families['timing_validity']=_family([{'state':'pass' if timing.get('valid') is True else 'insufficient'}],
        'existing required recorder, identity, clock, availability, quiet partition and interval parity evidence')
    unexpected=(traffic.get('generator_contract',{}).get('retained_generator') or {}).get('unexpected_outcomes')
    families['application_outcomes']=_family([{'state':('fail' if unexpected else 'pass') if type(unexpected) is int and unexpected>=0 else 'insufficient'}],
        'retained generator declared unexpected outcomes; traffic numerical agreement separate')
    return families
