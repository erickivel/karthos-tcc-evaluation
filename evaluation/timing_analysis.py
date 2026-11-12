"""Offline timing diagnostics. Evidence is read only; no rescoring or live calls.

Start, completion, export and observation time are distinct. Quiet deltas are
endpoint accounting only, and never prove absence of requests still in flight.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

from .evidence_v4 import raw_spans, grpc_outcome
from .sample_records import iter_records
from .series_identity import request_failed
from .scoring import stamp, counter_window, exact_query_parity, capture_integrity, mesh_source, DEFAULTS, vm_source, api_samples, selected, weighted_duration as api_weighted_duration

SCHEMA_VERSION = 'timing-verifier1.0.1'


def read(path, default=None):
    return json.loads(Path(path).read_text()) if Path(path).exists() else default


def weighted_duration(partitions):
    """Weight only matching populations; missing weights are unknown, never zero."""
    if not partitions or any(type(p.get('count')) not in (int,float) or
            not math.isfinite(p['count']) or p['count'] < 0 or
            type(p.get('mean_ms')) not in (int,float) or not math.isfinite(p['mean_ms'])
            for p in partitions):
        return dict(state='incomplete',value_ms=None,reason='missing or invalid matching population weight')
    count=sum(p['count'] for p in partitions)
    return dict(state='available' if count else 'incomplete',count=count,
                value_ms=sum(p['mean_ms']*p['count'] for p in partitions)/count if count else None)


def summarize(events):
    durations=[e['duration_ms'] for e in events if e.get('duration_ms') is not None]
    return dict(requests=len(events),outcome_errors=sum(e.get('outcome_failed') is True for e in events),
        outcome_unknown=sum(e.get('outcome_failed') is None for e in events),
        connector_errors=sum(e.get('connector_failed') is True for e in events),
        duration_ms=sum(durations)/len(durations) if durations else None,
        duration_population=len(durations),identities=[e['identity'] for e in events])


def populations(events, start, end):
    return dict(event_start=summarize([e for e in events if e.get('start') is not None and start<=e['start']<end]),
        completion=summarize([e for e in events if e.get('completion') is not None and start<=e['completion']<end]),
        crosses_start=[e['identity'] for e in events if e.get('start') is not None and e['start']<start<=e['completion']],
        crosses_end=[e['identity'] for e in events if e.get('start') is not None and e['start']<end<=e['completion']],
        bounds_seconds=[start,end],interval='[start,end)')


def observer_events(path, run_id):
    events=[]; errors=[]
    for file in sorted((path/'logs').glob('*eval-observer.log')):
        for line_number,line in enumerate(file.read_text(errors='replace').splitlines(),1):
            offset=line.find('{')
            if offset<0: continue
            try:
                row=json.loads(line[offset:])
                if row.get('boundary') not in ('frontend','checkout') or 'observed_at' not in row: continue
                # Internal checkout calls do not propagate the generator run ID.
                if row['boundary']=='frontend' and run_id and row.get('run_id')!=run_id: continue
                end=stamp(row['observed_at'])
                duration=float(row['request_time_seconds'])
                if not math.isfinite(duration) or duration<0: raise ValueError('invalid proxy duration')
                protocol='grpc' if row['boundary']=='checkout' else 'http'
                outcome=request_failed(dict(row,request_protocol=protocol,grpc_response_status=row.get('grpc_status')))
                events.append(dict(identity=f'{file.name}:{line_number}',boundary=row['boundary'],
                    start=end-duration,completion=end,duration_ms=duration*1000,
                    start_inferred=True,start_uncertainty_seconds=.002,
                    completion_precision_seconds=.001,
                    timing_semantics='nginx completion minus request_time; includes proxy and upstream transport',
                    outcome_failed=outcome,status=row.get('status'),grpc_status=row.get('grpc_status'),uri=row.get('uri'),
                    run_id=row.get('run_id'),request_id=row.get('request_id'),traceparent=row.get('traceparent')))
            except (ValueError,TypeError,KeyError) as exc:
                errors.append(dict(file=file.name,line=line_number,error=str(exc)))
    return events,errors


def trace_events(path):
    identities={}; conflicts=set(); events=[]
    for span in raw_spans(path/'structured/gateway/raw'):
        identity=(span['trace_id'],span['span_id'])
        if identity in identities and identities[identity]!=span: conflicts.add(identity)
        identities[identity]=span
    for identity,span in sorted(identities.items()):
        if span['kind']!='server' or span['service'] not in ('checkout','checkoutservice') or not span.get('name','').endswith('/PlaceOrder'): continue
        parent_key=(span['trace_id'],span.get('parent_id'))
        parent=identities.get(parent_key)
        paired=bool(parent and parent['kind']=='client' and parent['service']=='frontend')
        grpc=grpc_outcome(span['attributes'])
        events.append(dict(identity='/'.join(identity),trace_id=span['trace_id'],span_id=span['span_id'],
            parent_id=span.get('parent_id'),paired_frontend=paired,target_service=span['service'],
            conflicting_identity=identity in conflicts or parent_key in conflicts,
            start=span['start'],completion=span['end'],duration_ms=(span['end']-span['start'])*1000,
            client_start=parent['start'] if paired else None,client_completion=parent['end'] if paired else None,
            connector_failed=span['failed'] or bool(paired and parent['failed']),
            outcome_failed=grpc['grpc_outcome_failed'],**grpc))
    return events,sorted('/'.join(key) for key in conflicts)


def _quiet_endpoint(document,source):
    reasons=[]; records=document.get('records',[])
    if document.get('stable') is not True: reasons.append('stability not established within 90s bound')
    if len(records)<2: return None,['missing repeated quiet observations']+reasons
    interval=document.get('measured_collection_interval_seconds')
    try:
        last=records[-1]; previous=records[-2]
        if not interval or stamp(last['observed_at'])-stamp(previous['observed_at'])<interval:
            reasons.append('quiet checks do not span measured collection interval')
        values=last['values']
        if not values or values!=previous['values']: reasons.append('missing or unstable full partitions')
        for key,value in values.items():
            labels=json.loads(key)
            if labels.get('source')!=source: reasons.append('wrong or missing source partition')
            if not math.isfinite(float(value)): reasons.append('invalid counter')
            for record in (previous,last):
                observed=stamp(record['observed_at']); measured=record.get('sample_times',{}).get(key)
                if measured is None or not 0<=observed-stamp(measured)<=60:
                    reasons.append('missing or stale counter timestamp')
        for before,after in zip(records,records[1:]):
            for key in before.get('values',{}).keys() & after.get('values',{}).keys():
                if after['values'][key]<before['values'][key]: reasons.append('counter reset within quiet checks')
    except (KeyError,ValueError,TypeError) as exc:
        return None,reasons+['invalid quiet evidence: '+str(exc)]
    return values,list(dict.fromkeys(reasons))


def quiet_delta(before,after,source):
    baseline,errors_before=_quiet_endpoint(before,source)
    final,errors_after=_quiet_endpoint(after,source)
    reasons=errors_before+errors_after; deltas={}; resets=[]; missing=[]
    if baseline is not None and final is not None:
        missing=sorted(baseline.keys() ^ final.keys())
        if missing: reasons.append('missing or new full counter partitions between endpoints')
        for key in baseline.keys() & final.keys():
            delta=float(final[key])-float(baseline[key])
            if delta<0: resets.append(key)
            deltas[key]=delta
        if resets: reasons.append('counter reset between quiet endpoints')
    exact=bool(deltas) and not reasons
    statuses={}
    for key in deltas:
        labels=json.loads(key)
        if source=='traces.servicegraph':
            failed=str(labels.get('failed','')).lower()
            statuses[key]=True if failed=='true' else (False if failed=='false' else None)
        elif source=='mesh.istio': statuses[key]=request_failed(labels)
        else: statuses[key]=None
    unknown=[key for key,failed in statuses.items() if failed is None]
    known_errors=sum(deltas[key] for key,failed in statuses.items() if failed is True)
    return dict(state='available' if exact else 'incomplete',requests=sum(deltas.values()) if exact else None,
        endpoint_requests_delta=sum(deltas.values()) if deltas else None,
        errors=known_errors if exact and not unknown else None,endpoint_errors_delta=known_errors if not unknown else None,
        known_error_requests=known_errors if exact else None,
        unknown_status_requests=sum(deltas[key] for key in unknown) if exact else None,
        unknown_status_partitions=unknown,
        error_semantics='paired CLIENT OR SERVER OTel ERROR encoded in failed label' if source=='traces.servicegraph' else 'source status policy: HTTP5xx or known nonzero gRPC status; missing status remains unknown',
        exact=exact,partition_deltas=deltas,resets=resets,missing_partitions=missing,reasons=list(dict.fromkeys(reasons)),
        semantics='quiet source-partition counter delta; eventual endpoint accounting, not event-time window fidelity',
        limitations=['in-flight requests and background traffic require independent evidence',
                    'resets entirely between observations cannot be excluded by endpoint values alone'])


def audit_quiet_interval_counters(path,before,after,source,result):
    """Audit captured cumulative samples across both quiet endpoints, including drain."""
    baseline=before.get('records',[{}])[-1] if before.get('records') else {}
    final=after.get('records',[{}])[-1] if after.get('records') else {}
    canonical=lambda value:json.dumps(value,sort_keys=True,separators=(',',':'))
    expected={canonical(json.loads(key)) for key in baseline.get('values',{})}
    points=defaultdict(dict); conflicts=[]; resets=[]; missing=[]
    start_times=baseline.get('sample_times',{});end_times=final.get('sample_times',{})
    lo=min((stamp(t) for t in start_times.values()),default=None)
    hi=max((stamp(t) for t in end_times.values()),default=None)
    if lo is not None and hi is not None:
        for row in iter_records(Path(path)/'vm-raw.ndjson'):
            metric=row.get('metric',{})
            if metric.get('__name__')!='servicegraph_request_count' or metric.get('source')!=source:continue
            if metric.get('edge_server') not in ('checkout','checkoutservice') or metric.get('edge_client')!='frontend':continue
            key=canonical(metric)
            for at,value in zip(row.get('timestamps',[]),row.get('values',[])):
                t=at/1000
                if not lo<=t<=hi:continue
                if t in points[key] and points[key][t]!=value:
                    conflicts.append(dict(identity=key,timestamp=t,values=[points[key][t],value]))
                points[key][t]=value
        missing=sorted(key for key in expected if key not in points)
        for key,values in points.items():
            for previous,current in zip(sorted(values.items()),sorted(values.items())[1:]):
                if current[1]<previous[1]:resets.append(dict(identity=key,previous=previous,current=current))
    else:missing=sorted(expected)
    unexpected=sorted(set(points)-expected)
    result.update(captured_resets=resets,captured_counter_conflicts=conflicts,
        captured_missing_partitions=missing,captured_unexpected_partitions=unexpected,
        captured_audit_bounds_seconds=[lo,hi],
        captured_audit_semantics='all captured selected-source request counter samples between quiet endpoint measurement times, including drain; unobserved resets remain unprovable')
    if resets or conflicts or missing or unexpected or lo is None or hi is None:
        result.update(exact=False,state='incomplete',requests=None,errors=None,known_error_requests=None,unknown_status_requests=None)
        if resets:result['reasons'].append('captured source counter reset between quiet endpoints, including workload/drain')
        if conflicts:result['reasons'].append('conflicting captured counter samples')
        if missing or unexpected:result['reasons'].append('missing or unexpected captured source partitions')
        if lo is None or hi is None:result['reasons'].append('missing quiet measurement bounds for captured reset audit')
    return result


def _visible_measurements(row):
    """Response identities remain full; values are excluded from measurement identity."""
    source=row.get('source'); response=row.get('response',{})
    if isinstance(response,dict) and 'response' in response: response=response['response']
    if source=='api' and isinstance(response,dict):
        samples=response.get('samples',response.get('data',{}).get('samples',[]) if isinstance(response.get('data'),dict) else [])
        for sample in samples:
            identity={k:v for k,v in sample.items() if k not in ('value','value_label','valueLabel')}
            yield json.dumps(identity,sort_keys=True,separators=(',',':'))
    elif source=='vm':
        rows=response if isinstance(response,list) else []
        if isinstance(response,dict): rows=response.get('data',{}).get('result',[]) if isinstance(response.get('data'),dict) else []
        if isinstance(response,str): rows=[json.loads(line) for line in response.splitlines() if line.strip()]
        for series in rows:
            metric=series.get('metric',{})
            stamps=series.get('timestamps')
            if stamps is not None:
                for at in stamps: yield json.dumps(dict(metric=metric,timestamp_ms=at),sort_keys=True,separators=(',',':'))
            else:
                for pair in series.get('values',[series['value']] if 'value' in series else []):
                    yield json.dumps(dict(metric=metric,timestamp=pair[0]),sort_keys=True,separators=(',',':'))
    elif source=='collector-tail':
        # File observation is evidence of a byte range, never metric ingestion.
        body=row.get('body','')
        if body:
            for line in body.splitlines():
                if not line.strip(): continue
                document=json.loads(line)
                for resource in document.get('resourceSpans',[]):
                    for scope in resource.get('scopeSpans',[]):
                        for span in scope.get('spans',[]):
                            yield json.dumps(dict(kind='span',trace_id=span.get('traceId'),span_id=span.get('spanId'),start=span.get('startTimeUnixNano'),end=span.get('endTimeUnixNano')),sort_keys=True,separators=(',',':'))
                for resource in document.get('resourceMetrics',[]):
                    for scope in resource.get('scopeMetrics',[]):
                        for metric in scope.get('metrics',[]):
                            for kind in ('sum','gauge','histogram'):
                                for point in metric.get(kind,{}).get('dataPoints',[]):
                                    identity=dict(kind=kind,name=metric['name'],resource=resource.get('resource'),scope=scope.get('scope'),attributes=point.get('attributes'),time=point.get('timeUnixNano'),start=point.get('startTimeUnixNano'))
                                    yield json.dumps(identity,sort_keys=True,separators=(',',':'))


def availability_intervals(rows):
    observations=[]; seen=defaultdict(set); previous={}; errors=[]
    for row in rows:
        source=row.get('source'); query=row.get('query_id',row.get('url')) or json.dumps({k:row[k] for k in ('metric_name','params','component','filename','pod','pod_uid','inode') if k in row},sort_keys=True); stream=(source,query)
        if source=='collector-tail':stream=(source,query,row.get('node_name'),row.get('collector_uid'))
        # A negative query for time A cannot bound first visibility at time B.
        # Scope API polling bounds to the complete fixed query, including
        # selector, window and evaluation point. Legacy rows remain readable.
        if source == 'api':
            stream = (source, query, json.dumps(row.get('params', {}), sort_keys=True))
        try:
            requested=int(row['requested_at_ns'])/1e9; received=int(row['received_at_ns'])/1e9
            if received<requested: raise ValueError('response precedes request')
            # An HTTP/query error is not negative evidence.
            if row.get('error') or row.get('status',200)>=400: continue
            for identity in _visible_measurements(row):
                if identity in seen[stream]: continue
                observations.append(dict(source=source,query_id=query,identity=identity,
                    **{key:row[key] for key in ('node_name','collector_uid','collector_kind') if key in row},
                    evaluation_at=row.get('evaluation_at'), probe_policy=row.get('probe_policy'),
                    possible_first_availability_seconds=[previous.get(stream),received],
                    first_positive_request_seconds=requested,first_positive_received_seconds=received,
                    lower_bound_semantics='previous negative request initiation; unbounded if first observation',
                    interpretation='first observed availability; collector-tail is file visibility only'))
                seen[stream].add(identity)
            # On the next poll an unseen identity was absent from this response.
            previous[stream]=requested
        except (ValueError,TypeError,KeyError) as exc: errors.append(str(exc))
    return dict(state='available' if observations and not errors else 'incomplete',observations=observations,errors=errors,
        exact_ingestion_time_supported=False,
        semantics='conservative polling interval, no monotonic availability or exact processing timestamp assumed')


def source_window_counts(path,start,end,source):
    partitions=defaultdict(dict); invalid=[]
    for row in iter_records(path/'vm-raw.ndjson'):
        metric=row.get('metric',{})
        if metric.get('__name__')!='servicegraph_request_count' or metric.get('source')!=source: continue
        # Same selected edge as quiet_counter; client retained in identity.
        if metric.get('edge_server') not in ('checkout','checkoutservice'): continue
        key=json.dumps(metric,sort_keys=True,separators=(',',':'))
        for at,value in zip(row.get('timestamps',[]),row.get('values',[])):
            timestamp=at/1000
            if timestamp in partitions[key] and partitions[key][timestamp]!=value:
                invalid.append(dict(partition=key,timestamp=timestamp,reason='conflicting same-time samples'))
            partitions[key][timestamp]=value
    checks=[]
    for key,points in sorted(partitions.items()):
        check=counter_window(sorted(points.items()),start,end,alignment=45)
        checks.append(dict(identity=json.loads(key),**check))
    complete=bool(checks) and not invalid and all(x.get('increase') is not None and not x.get('resets') for x in checks)
    outcomes=[(x,request_failed(x['identity'])) for x in checks]
    return dict(state='available' if complete else 'incomplete',
        requests=sum(x['increase'] for x in checks) if complete else None,partitions=checks,conflicts=invalid,
        status_policy_errors=sum(x['increase'] for x,failed in outcomes if failed is True) if complete and all(failed is not None for _,failed in outcomes) else None,
        connector_failed_partition_requests=sum(x['increase'] for x in checks if str(x['identity'].get('failed','')).lower()=='true') if complete else None,
        exact_event_time_count=False,semantics='stored cumulative-counter bracketing; scrape boundaries differ from event time',
        missing_partitions='unknown without independent expected partition inventory')


def parity_evidence(path,source):
    parity=read(path/'parity.json',{})
    if parity:
        return dict(state='retained',origin='parity.json',checks=parity,recomputed=False,interval=read(path/'interval.json',{}))
    verification=read(path/'verification.json',{})
    counts=verification.get('counts_path',verification.get('counts',{}))
    retained={key:value for key,value in counts.items() if key.startswith('exact_')}
    if retained: return dict(state='retained',origin='verification.json',checks=retained,recomputed=False)
    checks={}
    for name in ('request-count','error-count','duration'):
        file='api-'+name+'.json'
        if (path/file).exists() and (path/'vm-parity'/file).exists(): checks[name]=exact_query_parity(path,file,source)
    return dict(state='available' if checks else 'incomplete',checks=checks,
        semantics='existing full-series product-rendered query parity helper; no generator oracle needed')


def generator_contract(path,manifest,start,end):
    expected=manifest.get('probe_expected_checkout_operations',manifest.get('probe_contract',{}).get('expected_checkout_operations'))
    if manifest.get('kind')=='confirmation':
        verification=read(path/'verification.json',{})
        valid=verification.get('evidence',{}).get('valid') is True
        terminals={};conflicts=set()
        for row in iter_records(path/'ledger.ndjson'):
            if row.get('run_id')!=manifest.get('run_id') or not row.get('request_id'):
                continue
            identity=row['request_id']
            if identity in terminals and terminals[identity]!=row:
                conflicts.add(identity)
            terminals[identity]=row
        checkout=[row for row in terminals.values() if row.get('phase')=='checkout'
                  or row.get('route') in ('checkout','checkout_expired')]
        return dict(state='available' if valid else 'incomplete',expected_checkout_operations=None,
            observed_checkout_operations=verification.get('source',{}).get('requests'),
            retained_generator=verification.get('generator'),retained_evidence=verification.get('evidence'),
            checkout_request_ids=[row['request_id'] for row in checkout],
            checkout_trace_ids=[row.get('actual_trace_id',row.get('trace_id')) for row in checkout],
            conflicting_request_ids=sorted(conflicts),
            reasons=[] if valid else ['confirmation verification evidence validity absent or incomplete'],
            requires_historical_schedule_oracle=False,semantics='retain existing confirmation artifact and full-accounting evidence; numerical verdict remains separate')
    new_probe=manifest.get('kind')=='probe'
    records=[r for r in iter_records(path/'ledger.ndjson') if r.get('run_id')==manifest.get('run_id') and (new_probe or start<=stamp(r['started_at'])<end)]
    attempts=[r for r in iter_records(path/'attempts.ndjson') if r.get('run_id')==manifest.get('run_id') and (new_probe or start<=stamp(r['started_at'])<end)]
    terminals={}; conflicts=[]
    for record in records:
        key=record['request_id']
        if key in terminals and terminals[key]!=record: conflicts.append(key)
        terminals[key]=record
    checkout=[r for r in terminals.values() if r.get('phase')=='checkout' or (not new_probe and r.get('phase') is None and r.get('route') in ('checkout','checkout_expired'))]
    attempt_ids={r['request_id'] for r in attempts}
    completion_ids={r['request_id'] for r in terminals.values() if r.get('completed') is True}
    reasons=[]
    if expected is None: reasons.append('no explicit probe checkout operation contract; historical schedule not rescored')
    elif len(checkout)!=expected: reasons.append('checkout terminal count differs from explicit probe contract')
    if conflicts: reasons.append('conflicting terminal identities')
    if not attempts or attempt_ids!=set(terminals): reasons.append('attempt/terminal identity mismatch or missing attempts')
    if set(terminals)!=completion_ids: reasons.append('noncompleted terminal operations')
    if any(r.get('outcome_ok') is not True for r in terminals.values()): reasons.append('unexpected or unknown generator outcome')
    return dict(state='available' if not reasons else 'incomplete',expected_checkout_operations=expected,
        observed_checkout_operations=len(checkout),total_terminals=len(terminals),attempts=len(attempt_ids),
        conflicting_request_ids=conflicts,reasons=reasons,requires_historical_schedule_oracle=False,
        checkout_request_ids=[r['request_id'] for r in checkout],
        checkout_trace_ids=[r.get('actual_trace_id',r.get('trace_id')) for r in checkout],
        population_semantics='all captured run identities including completion tails; fixed-window memberships reported separately')


def linked_checkout_traces(report):
    capture=report.get('whole_capture_source_identity_coverage',{})
    expected=capture.get('checkout_trace_ids',[])
    generator=report.get('generator_contract',{})
    operations=generator.get('checkout_request_ids',[])
    if (not expected or not all(isinstance(i,str) and i for i in expected)
            or len(expected)!=len(set(expected)) or len(expected)!=len(operations)
            or generator.get('conflicting_request_ids')
            or not set(expected)<=set(capture.get('paired_trace_ids',[]))):
        return None
    return sorted(expected)


def scoped_mesh_checkout_partitions(whole, namespace, service):
    for row in whole.get('raw_mesh_source',{}).get('series',[]):
        labels=row.get('labels',{})
        if (row.get('metric')=='istio_requests_total' and labels.get('reporter')=='destination'
                and labels.get('source_workload_namespace')==namespace
                and labels.get('destination_service_namespace')==namespace
                and labels.get('destination_service_name')==service
                and labels.get('source_workload')=='frontend'):
            yield row


def fixed_window_error_reference(manifest,report,observers,traces,start,end):
    source=manifest.get('source_id')
    service={'boutique':'checkoutservice','astronomy':'checkout'}.get(manifest.get('demo'))
    namespace=manifest.get('namespace')
    scope={'bounds_seconds':[start,end],'source_id':source,'namespace':namespace,
           'caller':'frontend','target':service}
    def insufficient(reason):
        return dict(scope,state='insufficient',reason=reason)
    if not namespace or not service or report.get('recorder_identity_valid') is not True:
        return insufficient('independent scoped checkout recorder or identity evidence unavailable')
    checkout=[row for row in observers if row['boundary']=='checkout' and (row.get('uri') or '').endswith('/PlaceOrder')]
    started=[row for row in checkout if start<=row['start']<end]
    completed=[row for row in checkout if start<=row['completion']<end]
    if not started or {row['identity'] for row in started}!={row['identity'] for row in completed}:
        return insufficient('checkout event membership differs across start and completion bounds')

    if source=='traces.servicegraph':
        linked=set(linked_checkout_traces(report) or [])
        selected=[row for row in traces if row.get('target_service')==service
                  and (start<=row['start']<end or start<=row['completion']<end)]
        identities={row['trace_id'] for row in selected}
        observer_ids=set()
        for row in completed:
            match=re.fullmatch(r'[0-9a-f]{2}-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}',
                               row.get('traceparent') or '')
            if match:
                observer_ids.add(match[1])
        if (not identities or not identities<=linked or identities!=observer_ids
                or len(identities)!=len(completed) or len(identities)!=len(selected)
                or any(not row['paired_frontend'] or row['conflicting_identity']
                       or not start<=row['start']<=row['completion']<end for row in selected)):
            return insufficient('no one-to-one scoped checkout observer, generator and paired trace identity join')
        return dict(scope,state='available',errors=sum(row['connector_failed'] for row in selected),
                    requests=len(selected),trace_ids=sorted(identities),
                    policy='paired frontend client or checkout server OTel ERROR')

    if source=='mesh.istio':
        whole=report.get('windows',{}).get('whole',{})
        candidates=[(row,request_failed(row['labels']))
                    for row in scoped_mesh_checkout_partitions(whole,namespace,service)]
        if (not candidates or any(failed is None or row.get('increase') is None or row.get('resets')
                                  for row,failed in candidates)
                or any(type(row.get('outcome_failed')) is not bool for row in completed)):
            return insufficient('independent checkout statuses or scoped destination Istio partitions unavailable')
        first=min(row['first'] for row,_ in candidates)
        last=max(row['last'] for row,_ in candidates)
        if any(row['completion']>=first and row['start']<=last
               and (row['start']<start or row['completion']>=end) for row in checkout):
            return insufficient('checkout events cross the fixed window or the source counter boundary slack')
        requests=sum(row['increase'] for row,_ in candidates)
        errors=sum(row['outcome_failed'] for row in completed)
        if requests!=len(completed) or errors!=sum(row['increase'] for row,failed in candidates if failed):
            return insufficient('scoped destination counters and independent checkout population disagree')
        return dict(scope,state='available',errors=errors,requests=requests,source_bracket_seconds=[first,last],
                    policy='checkout HTTP 5xx or present nonzero gRPC status')
    return insufficient('no supported independent checkout error policy')


def required_evidence_gate(report):
    """Missing required observations fail the evidence gate; numeric differences do not."""
    reasons=[]
    agents=report.get('agent_evidence',{})
    count=report.get('manifest',{}).get('expected_nodes',1)
    multi=count>1 or agents.get('multinode') or any(key.startswith('agent:') for key in report.get('structured_capture',{}).get('components',{}))
    if multi:
        expected=agents.get('expected_nodes',[])
        identities=agents.get('agents',[])
        if agents.get('valid') is not True or len(expected)!=len(set(expected)) or not expected or (count>1 and len(expected)!=count) or {a.get('node') for a in identities}!=set(expected) or len(identities)!=len(expected) or len({a.get('collector_uid') for a in identities if a.get('collector_uid')})!=len(expected):
            reasons.append('multi-node Agent archive or independent source identity evidence incomplete')
    if not report.get('independent_valid'): reasons.append('independent generator/source/observer capture evidence incomplete')
    if not report.get('clock',{}).get('supports_1s_boundary_claims'):
        reasons.append('clock bound missing or not strictly below 250ms')
    if report.get('availability_status',{}).get('valid') is not True or report.get('availability',{}).get('state')!='available':
        reasons.append('availability recorder lifecycle or observations incomplete')
    if report.get('eventual_completeness',{}).get('exact') is not True:
        reasons.append('quiet source-partition endpoint evidence incomplete or reset observed')
    required={'exact_request_count_api','exact_error_count_api','exact_duration_api'}
    aliases={'request-count':'exact_request_count_api','error-count':'exact_error_count_api','duration':'exact_duration_api'}
    def incomplete(value):
        if isinstance(value,dict):
            return value.get('state')=='incomplete' or any(incomplete(v) for v in value.values())
        return isinstance(value,list) and any(incomplete(v) for v in value)
    for name,window in report.get('windows',{}).items():
        checks=window.get('api_storage_parity',{}).get('checks',{})
        checks={aliases.get(key,key):value for key,value in checks.items()}
        if not required<=checks.keys() or any(not isinstance(checks.get(key),dict) or checks[key].get('state') not in ('pass','fail','available') or incomplete(checks.get(key)) for key in required):
            reasons.append('missing required full-series parity evidence for '+name)
    if not report.get('windows'): reasons.append('missing explicit fixed windows')
    return dict(valid=not reasons,state='available' if not reasons else 'incomplete',reasons=reasons,
                numerical_agreement_required=False)


def _analyze_window(path):
    path=Path(path); manifest=read(path/'manifest.json',{})
    report=dict(schema_version=SCHEMA_VERSION,path=str(path),manifest=manifest,state='incomplete',
        evidence_requirements=['explicit manifest start/end', 'clock_uncertainty_seconds < 0.250 for 1s boundary claims',
            'complete independent frontend and checkout log streams', 'source partitions and no observed resets',
            'stable quiet-before/after counters within bounded 90s checks for eventual completeness',
            'both span identities and parent identities for trace pairing',
            'availability request/receive timestamps for first-observed intervals'],
        unsupported_measurements=dict(exact_ingestion_time='unsupported',frontend_equals_internalcalls='unsupported',mesh_grpc_outcome_without_status='unsupported'))
    uncertainty=manifest.get('clock_uncertainty_seconds')
    valid=type(uncertainty) in (int,float) and math.isfinite(uncertainty) and uncertainty>=0
    report['clock']=dict(state='available' if valid else 'incomplete',uncertainty_seconds=uncertainty,
        supports_1s_boundary_claims=bool(valid and uncertainty<.25))
    if manifest.get('start') is None or manifest.get('end') is None:
        report['reasons']=['missing explicit manifest bounds']; return report
    start,end=stamp(manifest['start']),stamp(manifest['end'])
    if end<=start: report['reasons']=['non-positive explicit window']; return report
    source=manifest.get('source_id','')
    observers,observer_errors=observer_events(path,manifest.get('run_id'))
    traces,conflicts=trace_events(path)
    report['events']=dict(observers=observers,traces=traces)
    report['generator_contract']=generator_contract(path,manifest,start,end)
    verification=read(path/'verification.json',{})
    report['retained_secondary_checks']={key:verification[key] for key in ('resources','entities','runs_on_edges','capture') if key in verification}
    report['integrity']=dict(observer_parse_errors=observer_errors,trace_identity_conflicts=conflicts,
        observer_stream_completeness='not established by record presence alone',
        checkout_run_attribution='all checkout calls in bounds; may include background callers; frontend run ID filter applied separately')
    bounds={'whole':(start,end)}
    if abs(end-start-120)<.01: bounds['inner']=(start+30,start+90)
    windows={}
    for name,(lo,hi) in bounds.items():
        window=dict(bounds_seconds=[lo,hi],interval='[start,end)',observers={})
        for boundary in ('frontend','checkout'):
            events=[event for event in observers if event['boundary']==boundary]
            window['observers'][boundary]=dict(populations(events,lo,hi),evidence_present=bool(events))
        checkout=[event for event in observers if event['boundary']=='checkout' and (event.get('uri') or '').endswith('/PlaceOrder')]
        window['checkout_place_order']=dict(populations(checkout,lo,hi),evidence_present=bool(checkout))
        paired=[event for event in traces if event['paired_frontend'] and not event['conflicting_identity']]
        window['raw_trace_source']=dict(populations(paired,lo,hi),
            unpaired=populations([event for event in traces if not event['paired_frontend']],lo,hi),
            connector_error_policy='paired CLIENT OR SERVER OTel ERROR, distinct from HTTP/gRPC outcome',
            duration_semantics='paired checkout SERVER span duration; proxy and client durations remain separate')
        window['stored_source_counters']=source_window_counts(path,lo,hi,source)
        window['stored_histograms']=vm_source(path,lo,hi,source,DEFAULTS)
        window['stored_histograms']['population_equivalence']='not established by aggregate count agreement'
        if any(item.get('resets') for item in window['stored_histograms']['series']):
            window['stored_histograms']['state']='incomplete'
            window['stored_histograms']['reason']='observed counter reset; means retained descriptively'
        if source=='mesh.istio':
            window['raw_mesh_source']=mesh_source(path,lo,hi,DEFAULTS)
            window['raw_mesh_source']['timing_semantics']='destination proxy cumulative counters bracketed at acquisition times; not event timestamps'
            window['raw_mesh_source']['outcome_semantics']='HTTP 5xx or known nonzero gRPC status; missing gRPC status stays unknown'
        parity_path=path/'intervals'/name
        window['api_storage_parity']=parity_evidence(parity_path,source) if parity_path.exists() else (parity_evidence(path,source) if name=='whole' else dict(state='incomplete',reason='missing exact inner interval parity evidence'))
        api_path=parity_path if parity_path.exists() else (path if name=='whole' else None)
        window['api_weighted_duration']=api_weighted_duration(selected(api_samples(api_path,'api-duration.json')),selected(api_samples(api_path,'api-request-count.json'))) if api_path else dict(state='incomplete',value_ms=None,reason='no API data for predefined inner interval')
        window['api_weighted_duration']['population_equivalence']='not established; full-series weights retained'
        window['discrepancies']={}
        for population in ('event_start','completion'):
            checkout=window['observers']['checkout'][population]
            trace=window['raw_trace_source'][population]
            window['discrepancies'][population]=dict(checkout_minus_paired_trace=checkout['requests']-trace['requests'] if observers and traces else None,
                proxy_minus_server_duration_ms=checkout['duration_ms']-trace['duration_ms'] if checkout['duration_ms'] is not None and trace['duration_ms'] is not None else None,
                semantics='separate selected populations; no equality assumed; duration layers differ')
        windows[name]=window
    report['windows']=windows
    before,after=read(path/'quiet-before.json',{}),read(path/'quiet-after.json',{})
    report['eventual_completeness']=quiet_delta(before,after,source)
    audit_quiet_interval_counters(path,before,after,source,report['eventual_completeness'])
    report['eventual_completeness']['fixed_window_source_counts']={key:report['windows']['whole']['raw_trace_source'][key]['requests'] if traces else None for key in ('event_start','completion')}
    report['eventual_completeness']['fixed_window_checkout_observer_counts']={key:report['windows']['whole']['observers']['checkout'][key]['requests'] if observers else None for key in ('event_start','completion')}
    eventual=report['eventual_completeness']
    eventual['numerical_differences']={}
    for population in ('event_start','completion'):
        trace_pop=report['windows']['whole']['raw_trace_source'][population]
        observer_pop=report['windows']['whole']['observers']['checkout'][population]
        source_count=trace_pop['requests'] if traces else None
        source_errors=trace_pop['connector_errors'] if source=='traces.servicegraph' and traces else (observer_pop['outcome_errors'] if source=='mesh.istio' and observers and observer_pop['outcome_unknown']==0 else None)
        eventual['numerical_differences'][population]=dict(
            settled_minus_source_requests=eventual['requests']-source_count if eventual['requests'] is not None and source_count is not None else None,
            settled_minus_checkout_observer_requests=eventual['requests']-observer_pop['requests'] if eventual['requests'] is not None and observers else None,
            settled_minus_same_policy_errors=eventual['errors']-source_errors if eventual['errors'] is not None and source_errors is not None else None,
            error_reference='paired raw span connector ERROR union' if source=='traces.servicegraph' else 'checkout observer known HTTP/gRPC outcome; unknown prevents comparison',
            interpretation='diagnostic endpoint total minus explicit fixed-window population; numerical differences do not invalidate recorder evidence')
    report['availability']=availability_intervals(iter_records(path/'availability.ndjson'))
    report['api_storage_parity']=parity_evidence(path,source)
    report['availability_status']=read(path/'availability-status.json',{})
    report['capture_integrity']=capture_integrity(path,source,manifest.get('demo'))
    report['structured_capture']=read(path/'structured/status.json',{})
    from .evidence_v4 import agent_evidence
    report['agent_evidence']=agent_evidence(path)
    expected=manifest.get('probe_expected_checkout_operations')
    observed_frontend_ids={e.get('request_id') for e in observers if e['boundary']=='frontend'}
    ledger_ids={r.get('request_id') for r in iter_records(path/'ledger.ndjson') if r.get('run_id')==manifest.get('run_id')}
    report['frontend_terminal_identity_coverage']=dict(missing=sorted(ledger_ids-observed_frontend_ids),observed=len(observed_frontend_ids),expected=len(ledger_ids))
    checkout_in_capture=sum(e['boundary']=='checkout' for e in observers)
    report['checkout_observer_capture_operations']=checkout_in_capture
    report['independent_valid']=bool(report['generator_contract']['state']=='available' and report['capture_integrity']['valid'] and report['structured_capture'].get('valid') is True and not observer_errors and not conflicts and not (ledger_ids-observed_frontend_ids) and ((manifest.get('kind')=='confirmation' and checkout_in_capture>0) or (expected is not None and checkout_in_capture>=expected)))
    report['whole_capture_source_identity_coverage']=dict(paired_trace_ids=sorted({e['trace_id'] for e in traces if e['paired_frontend'] and not e['conflicting_identity']}),
        checkout_trace_ids=report['generator_contract'].get('checkout_trace_ids',[]),
        semantics='all captured identities including completion tails; not truncated to original event window')
    if manifest.get('kind') in ('probe','confirmation') and source=='traces.servicegraph':
        expected_traces=report['generator_contract'].get('checkout_trace_ids',[])
        captured=set(report['whole_capture_source_identity_coverage']['paired_trace_ids'])
        missing=[identity for identity in expected_traces if not identity or identity not in captured]
        report['whole_capture_source_identity_coverage']['missing_checkout_trace_ids']=missing
        if manifest.get('kind')=='probe':
            report['independent_valid']=report['independent_valid'] and bool(expected_traces) and not missing
    report['recorder_identity_valid']=report['independent_valid']
    if manifest.get('kind')=='confirmation':
        report['fixed_window_error_reference']=fixed_window_error_reference(manifest,report,observers,traces,start,end)
    report['required_evidence']=required_evidence_gate(report)
    report['independent_valid']=report['required_evidence']['valid']
    report['independent_valid_semantics']='required recorder/identity, clock, availability, quiet-partition and fixed-interval parity evidence; numerical agreement remains separate'
    report['state']='diagnostic' if report['independent_valid'] else 'incomplete'
    report['reasons']=list(report['required_evidence']['reasons'])
    if not valid: report['reasons'].append('missing clock uncertainty; time-sensitive claims incomplete')
    if not observers: report['reasons'].append('missing independent observer events')
    if not traces: report['reasons'].append('no raw paired trace proof; mesh may have no raw spans')
    report['fixed_window_fidelity']=dict(state='diagnostic',
        semantics='whole and predefined inner populations compared separately from quiet endpoint totals; no hidden moving windows',
        exact_counts_supported=False,reason='stored counter evaluation boundaries differ from request event boundaries')
    return report


def analyze_window(path):
    """Return an explicit incomplete result for unusable persisted evidence."""
    try:
        return _analyze_window(path)
    except (ValueError,TypeError,KeyError,OSError,EOFError) as exc:
        return dict(schema_version=SCHEMA_VERSION,path=str(path),state='incomplete',
                    independent_valid=False,reasons=['unusable timing evidence: '+str(exc)])
