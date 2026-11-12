"""Independent, destination-aware native request accounting; no business oracle."""
from .scoring import rows,stamp,state,observer_records
from .workloads.native_locust import trace_id_from_headers

CONTRACT='native-v2-final-request'


def native_boundary_scores(path,manifest):
    start,end=stamp(manifest['start']),stamp(manifest['end'])
    ledger=[r for r in rows(path/'ledger.ndjson') if r.get('run_id')==manifest['run_id'] and start<=stamp(r['started_at'])<end]
    observer_end=max([end]+[stamp(r['completed_at']) for r in ledger])+0.001
    observations=[r for r in observer_records(path,start,observer_end) if r.get('run_id')==manifest['run_id']]
    checks=[];boundaries={}
    for request in ledger:
        boundary=request.get('expected_boundary','unknown');rid=request['request_id']
        candidates=[r for r in observations if r.get('request_id')==rid and r.get('boundary')==boundary]
        actual=max(candidates,key=lambda r:stamp(r['observed_at'])) if candidates else None
        wire_trace=trace_id_from_headers({'traceparent':actual.get('traceparent')}) if actual else None
        reasons=[]
        if request.get('ledger_contract_version')!=CONTRACT:reasons.append('final-request ledger contract unavailable')
        if boundary not in ('frontend','agent'):reasons.append('unknown request destination')
        if not actual:reasons.append('independent destination observer record missing')
        if request.get('trace_context_evidence')!='response.request.headers' or not request.get('trace_id'):reasons.append('transmitted trace context unavailable')
        if not wire_trace or wire_trace!=request.get('trace_id'):reasons.append('ledger and observer trace context do not match')
        check=dict(request_id=rid,boundary=boundary,generator_status=request.get('status'),observer_status=actual.get('status') if actual else None,
            wire_observations=len(candidates),trace_id=request.get('trace_id'),wire_trace_id=wire_trace,evidence_valid=not reasons,reasons=reasons,
            state='incomplete' if reasons else ('pass' if int(actual['status'])==int(request['status']) else 'fail'))
        checks.append(check)
    for boundary in sorted(set(r['boundary'] for r in checks)):
        selected=[r for r in checks if r['boundary']==boundary]
        boundaries[boundary]=dict(expected_requests=len(selected),observed_requests=sum(r['observer_status'] is not None for r in selected),
            wire_observations=sum(r['wire_observations'] for r in selected),state=state(selected),checks=selected)
    valid=bool(checks) and all(r['evidence_valid'] for r in checks)
    frontend=boundaries.get('frontend',dict(expected_requests=0,observed_requests=0,wire_observations=0,state='not-applicable',checks=[]))
    return dict(contract=CONTRACT,state=state(checks) if checks else 'incomplete',valid=valid,
        scheduled_bounds=[start,end],observer_end=observer_end,requests=len(checks),boundaries=boundaries,checks=checks,
        frontend=dict(state=frontend['state'],checks=frontend['checks'],observed=frontend['observed_requests'],
            expected=frontend['expected_requests'],wire_observations=frontend['wire_observations']),
        interpretation='One native request event can cause multiple wire observations through redirects or transport handling. Counts are retained separately; status disagreements do not erase valid evidence.')
