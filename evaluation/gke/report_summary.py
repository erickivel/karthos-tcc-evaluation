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
            'numerical_agreement':{'state':report.get('numerical_agreement',{}).get('state')},
            'coverage':{'contract':{'valid':(report.get('coverage',{}).get('contract') or {}).get('valid')}},
            'thesis_answers':{key:{'state':value.get('state')} for key,value in report.get('thesis_answers',{}).items()},
            'traffic':{'report':{
                'recorder_identity_valid':traffic.get('recorder_identity_valid'),
                'windows':{'whole':{'checkout_place_order':{'evidence_present':calls.get('evidence_present'),'completion':{'requests':calls.get('completion',{}).get('requests')}}}},
                'eventual_completeness':{k:eventual.get(k) for k in ('state','requests','numerical_differences')},
                'whole_capture_source_identity_coverage':{'checkout_trace_ids':expected,'paired_trace_ids':[x for x in paired if isinstance(x,str) and x in expected_set]},
                'generator_contract':{k:generator.get(k,[]) for k in ('checkout_request_ids','conflicting_request_ids')}}}}
    payload=json.dumps(result,indent=2,sort_keys=True)+'\n'
    if len(payload.encode())>LIMIT:raise ValueError('orchestration summary exceeds explicit size bound')
    write(path/'orchestration-summary.json',payload)
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
