"""Offline protocol-v3 comparisons. Missing evidence is never reported as zero loss.

Counters use measured bracketing samples, including resets; they are not extrapolated.
Proxy durations include proxy/network time and are reported separately from span time.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from .sample_records import iter_records, evidence_path
from .series_identity import partition_key, dimensions, request_failed, container_lifecycle

PROTOCOL_VERSION = 3
VERIFIER_VERSION = "3.0.0"
ERROR_POLICY_VERSION = "outcome-v1"

DEFAULTS = dict(request_relative_tolerance=.15, request_absolute_tolerance=2,
                duration_relative_tolerance=.20, duration_absolute_tolerance_ms=5,
                resource_relative_tolerance=.20, cpu_absolute_tolerance_millicores=10,
                memory_absolute_tolerance_bytes=1048576, max_alignment_seconds=10,
                max_freshness_seconds=60, max_counter_boundary_seconds=45,
                max_mesh_counter_boundary_seconds=15)


def stamp(value):
    if isinstance(value, (int, float)):
        return float(value)
    value = str(value).strip()
    if ' +0000 UTC' in value:
        value = value.replace(' +0000 UTC', '+00:00').replace(' ', 'T', 1)
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def read(path, fallback=None):
    if not path.exists():
        return fallback
    return json.loads(path.read_text())


def rows(path):
    return list(iter_records(path))


def unique(items):
    return list({json.dumps(x, sort_keys=True): x for x in items}.values())


def state(checks):
    states = [c.get('state', 'incomplete') for c in checks]
    return 'fail' if 'fail' in states else ('incomplete' if not states or 'incomplete' in states else 'pass')


def compare(reference, observed, relative, absolute):
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in (reference, observed)):
        return dict(state='incomplete', reference=reference, observed=observed, reason='missing or nonfinite evidence')
    error = abs(observed-reference)
    bound = max(absolute, abs(reference)*relative)
    return dict(state='pass' if error <= bound else 'fail', reference=reference,
                observed=observed, absolute_error=error, bound=bound,
                relative_error=error/abs(reference) if reference else None,
                relative_tolerance=relative, absolute_tolerance=absolute)


def counter_window(points, start, end, alignment=10, temporality="cumulative"):
    if temporality!="cumulative":
        return dict(increase=None,reason="counter bracketing requires verified cumulative temporality",resets=0)
    points = sorted(set((float(t), float(v)) for t, v in points))
    by_time=defaultdict(set)
    for t,v in points:
        if not math.isfinite(t) or not math.isfinite(v) or v<0:
            return dict(increase=None,reason='invalid cumulative counter sample',resets=0)
        by_time[t].add(v)
    if any(len(values)>1 for values in by_time.values()):
        return dict(increase=None,reason='ambiguous cumulative values at one timestamp',resets=0)
    before = [(t,v) for t,v in points if t <= start]
    after = [(t,v) for t,v in points if t >= end]
    if not before or not after:
        return dict(increase=None, reason='no bracketing counter samples', resets=0)
    left, right = before[-1], after[0]
    if start-left[0] > alignment or right[0]-end > alignment:
        return dict(increase=None, reason='counter boundary alignment exceeded', resets=0)
    selected = [(t,v) for t,v in points if left[0] <= t <= right[0]]
    total = 0
    resets = 0
    for (_, old), (_, new) in zip(selected, selected[1:]):
        resets += new < old
        total += new if new < old else new-old
    return dict(increase=total, resets=resets, first=left[0], last=right[0],
                boundary_slack_seconds=[start-left[0],right[0]-end])


def ms(value, unit):
    scale = {'nanoseconds':1e-6, 'microseconds':.001, 'milliseconds':1, 'seconds':1000}
    return float(value)*scale[unit] if unit in scale else None


def weighted_duration(samples, weights):
    samples = unique(samples)
    groups = defaultdict(list)
    legacy = isinstance(weights, dict)
    for sample in samples:
        key = str(sample.get('labels', {}).get('failed', 'false')).lower() if legacy else partition_key(sample.get('labels', {}))
        groups[key].append(sample)
    count_samples = defaultdict(list)
    if not legacy:
        for sample in unique(weights):
            count_samples[partition_key(sample.get('labels', {}))].append(sample)
    values = {}; resolved_weights = {}
    times = {str(s.get('timestamp')) for s in samples}
    if len(times) > 1:
        return dict(value_ms=None, state='incomplete', reason='multiple duration evaluation timestamps')
    for key, part in groups.items():
        if len(part) != 1:
            return dict(value_ms=None, state='incomplete', reason='multiple duration evaluations per full partition')
        value = ms(part[0]['value'], part[0].get('unit'))
        if value is None:
            return dict(value_ms=None, state='fail', reason='unexpected duration unit')
        if legacy:
            weight = weights.get(key)
        else:
            candidates = count_samples.get(key, [])
            if len(candidates) != 1 or candidates[0].get('timestamp') != part[0].get('timestamp'):
                return dict(value_ms=None, state='incomplete', reason='missing or misaligned full-partition count')
            weight = candidates[0].get('value')
        if weight is None or not math.isfinite(float(weight)) or float(weight) < 0:
            return dict(value_ms=None, state='incomplete', reason='missing or invalid partition weight')
        values[key] = value; resolved_weights[key] = float(weight)
    expected = set(weights) if legacy else {k for k,v in count_samples.items() if any(float(s.get('value',0)) > 0 for s in v)}
    if not values or expected - set(values):
        return dict(value_ms=None, state='incomplete', reason='missing partition durations')
    count = sum(resolved_weights.values())
    return dict(value_ms=sum(values[k]*resolved_weights[k] for k in values)/count if count else None,
                state='pass' if count else 'incomplete', partition_weights=resolved_weights,
                semantics='sum of matching partition means times counts divided by matching counts')


def parse_spans(text):
    """Parse detailed OTel debug exporter format, with optional kubectl timestamps."""
    service = None
    current = None
    result = {}
    def finish():
        if current and current.get('Trace ID') and current.get('ID'):
            item = dict(current)
            item['service'] = service
            item['failed'] = item.get('Status code','').lower() == 'error'
            grpc=item.get('rpc.grpc.status_code')
            item['grpc_outcome_failed'] = grpc != '0' if grpc is not None else None
            result[(item['Trace ID'],item['ID'])] = item
    for line in text.splitlines():
        line = re.sub(r'^\d{4}-\d\d-\d\dT\S+\s+', '', line)
        if re.match(r'.*ResourceSpans #\d+',line):
            finish(); current = None; service = None
        match = re.search(r'-> service.name: Str\((.*?)\)',line)
        if match:
            service = match[1]
        if re.match(r'^\s*Span #\d+',line):
            finish(); current = {}
        elif current is not None:
            m = re.match(r'\s*(Trace ID|Parent ID|ID|Name|Kind|Start time|End time|Status code)\s*:\s*(.*)',line)
            if m:
                current[m[1]] = m[2].strip()
            m = re.search(r'-> rpc.grpc.status_code: Int\((\d+)\)',line)
            if m:
                current['rpc.grpc.status_code'] = m[1]
    finish()
    return result


def trace_source(path, start, end):
    spans = {}
    for log in sorted((path/'logs').glob('*.log')):
        spans.update(parse_spans(log.read_text(errors='replace')))
    selected = []
    for key,s in spans.items():
        if s.get('Kind','').lower() != 'server' or 'checkout' not in (s.get('service') or '').lower() or not s.get('Name','').endswith('/PlaceOrder'):
            continue
        try:
            t = stamp(s['Start time']); duration = (stamp(s['End time'])-t)*1000
        except (KeyError, ValueError):
            continue
        if start <= t < end:
            parent = spans.get((s['Trace ID'],s.get('Parent ID')))
            paired = bool(parent and parent.get('Kind','').lower()=='client' and 'frontend' in (parent.get('service') or '').lower())
            selected.append(dict(trace_id=key[0],span_id=key[1],parent_id=s.get('Parent ID'),
                                 paired_frontend=paired,failed=bool(s['failed'] or (paired and parent['failed'])),
                                 client_span_status=parent.get('Status code') if paired else None,
                                 server_span_status=s.get('Status code'),
                                 grpc_status=s.get('rpc.grpc.status_code'),grpc_outcome_failed=s.get('grpc_outcome_failed'),duration_ms=duration))
    paired = [x for x in selected if x['paired_frontend']]
    return dict(valid=bool(paired), requests=len(paired) if selected else None,
                errors=sum(x['failed'] for x in paired) if selected else None,
                duration_ms=sum(x['duration_ms'] for x in paired)/len(paired) if paired else None,
                unpaired=sum(not x['paired_frontend'] for x in selected), spans=selected,
                error_semantics='service_graph0.156 connector-native: client OR server OTel span status ERROR; raw gRPC outcome retained separately',
                grpc_outcome_errors=sum(x.get('grpc_outcome_failed') is True for x in paired),
                grpc_outcome_unknown=sum(x.get('grpc_outcome_failed') is None for x in paired),
                duration_semantics='checkout SERVER span duration; paired frontend client identifies selected edge')


def prometheus(text):
    for line in text.splitlines():
        match = re.match(r'^(istio_(?:requests_total|request_duration_milliseconds_(?:sum|count)))\{(.*?)\}\s+(\S+)',line)
        if not match:
            continue
        labels = dict(re.findall(r'(\w+)="((?:\\.|[^"\\])*)"',match[2]))
        try:
            val = float(match[3])
        except ValueError:
            continue
        if math.isfinite(val):
            yield match[1],labels,val


def mesh_source(path,start,end,thresholds):
    series = defaultdict(list)
    for row in rows(path/'istio-samples.ndjson'):
        for name,labels,val in prometheus(row.get('text','')):
            server = labels.get('destination_workload',labels.get('destination_service_name',''))
            client = labels.get('source_workload','')
            if labels.get('reporter')!='destination' or 'checkout' not in server or 'frontend' not in client:
                continue
            series[(row.get('pod'),name,json.dumps(labels,sort_keys=True))].append((stamp(row['captured_at']),val))
    totals = defaultdict(float); partitions=defaultdict(float); detail=[]; complete=bool(series)
    for (_,name,encoded),points in series.items():
        result=counter_window(points,start,end,thresholds.get('max_mesh_counter_boundary_seconds',15))
        labels=json.loads(encoded)
        detail.append(dict(metric=name,labels=labels,**result))
        if result['increase'] is None:
            complete=False; continue
        totals[name]+=result['increase']
        if name=='istio_requests_total':
            failed = request_failed(labels)
            if failed is None:
                complete=False; continue
            partitions[str(failed).lower()]+=result['increase']
    count=totals.get('istio_requests_total') if complete else None
    dur_count=totals.get('istio_request_duration_milliseconds_count')
    return dict(valid=any(name=='istio_requests_total' for _,name,_ in series),requests=count,errors=partitions.get('true',0) if complete else None,
                duration_ms=totals['istio_request_duration_milliseconds_sum']/dur_count if complete and dur_count else None,
                unpaired=0,series=detail,partitions=dict(partitions),duration_semantics='Istio destination proxy histogram')


def selected(samples):
    return [s for s in unique(samples) if 'checkout' in s.get('labels',{}).get('edge_server','')
            and 'frontend' in s.get('labels',{}).get('edge_client','')]


def api_samples(path,name):
    document=read(path/name,{})
    return document.get('data',{}).get('samples',[])


def api_count(samples,source):
    samples=selected(samples)
    if not samples:
        return dict(value=None,state='incomplete',reason='selected edge absent')
    if any(s.get('labels',{}).get('source')!=source for s in samples):
        return dict(value=None,state='fail',reason='wrong/missing provenance')
    if any(s.get('unit')!='count' for s in samples):
        return dict(value=None,state='fail',reason='counter unit must be count')
    partitions=defaultdict(list)
    for s in samples:
        partitions[json.dumps(s.get('labels',{}),sort_keys=True)].append(s)
    if any(len(v)!=1 for v in partitions.values()):
        return dict(value=None,state='incomplete',reason='repeated counter evaluation timestamps')
    return dict(value=sum(float(s['value']) for s in samples),state='pass')


def histogram_mean(series):
    partitions=defaultdict(dict)
    for row in series:
        metric=row.get('metric',{})
        name=metric.get('__name__','')
        if 'duration' not in name or not name.endswith(('_sum','_count')):
            continue
        key=(metric.get('entity_id'),partition_key(metric))
        kind='sum' if name.endswith('_sum') else 'count'
        part=partitions[key]
        if kind in part:
            return dict(state='incomplete',value_ms=None,reason='ambiguous histogram partition')
        part[kind]=row
    total=0.; count=0.; detail=[]
    for (_,key), part in partitions.items():
        if set(part)!= {'sum','count'} or any(x.get('increase') is None for x in part.values()):
            return dict(state='incomplete',value_ms=None,reason='missing matching histogram sum/count partition')
        numerator,denominator=part['sum'],part['count']
        converted=ms(numerator['increase'],numerator['metric'].get('karthos_unit'))
        if converted is None:
            return dict(state='incomplete',value_ms=None,reason='unknown histogram unit')
        # Both cumulative components must cover the same bracket endpoints.
        if any(numerator.get(k)!=denominator.get(k) for k in ('first','last')):
            return dict(state='incomplete',value_ms=None,reason='misaligned histogram sum/count boundaries')
        total+=converted; count+=denominator['increase']
        detail.append(dict(identity=json.loads(key),sum_ms=converted,count=denominator['increase']))
    return dict(state='pass' if count else 'incomplete',value_ms=total/count if count else None,partitions=detail)


def vm_source(path,start,end,source,thresholds):
    series=defaultdict(list); wrong=[]
    for row in rows(path/'vm-raw.ndjson'):
        metric=row.get('metric',{})
        if 'checkout' not in metric.get('edge_server','') or 'frontend' not in metric.get('edge_client',''):
            continue
        if metric.get('source')!=source:
            wrong.append(metric); continue
        for t,v in zip(row.get('timestamps',[]),row.get('values',[])):
            series[json.dumps(metric,sort_keys=True)].append((float(t)/1000,float(v)))
    totals=defaultdict(float); parts=defaultdict(float); detail=[]; missing=set()
    for encoded,points in series.items():
        metric=json.loads(encoded); name=metric['__name__']; result=counter_window(points,start,end,thresholds.get('max_counter_boundary_seconds',45))
        detail.append(dict(metric=metric,**result))
        if result['increase'] is None:
            missing.add(name); continue
        totals[name]+=result['increase']
        if name.endswith('_count') and 'duration' in name:
            parts[metric.get('failed','false')]+=result['increase']
    def lookup(suffix):
        names=[n for n in totals if suffix(n)]
        return sum(totals[n] for n in names) if names and not any(suffix(n) for n in missing) else None
    count=lookup(lambda n:'duration' not in n and ('request_count' in n or 'request_total' in n) and 'error' not in n and 'failed' not in n)
    errors=lookup(lambda n:'error' in n or 'failed' in n)
    duration_sum=lookup(lambda n:'duration' in n and n.endswith('_sum'))
    duration_count=lookup(lambda n:'duration' in n and n.endswith('_count'))
    histogram=histogram_mean(detail)
    # Mesh error views use the preserved request counter, never historical ingest-derived counters.
    error_derivation=dict(state='incomplete',reason='not a mesh query view')
    if source=='mesh.istio':
        request_rows=[x for x in detail if x['metric']['__name__']=='servicegraph_request_count']
        classified=[(x,request_failed(x['metric'])) for x in request_rows]
        if classified and all(outcome is not None and row['increase'] is not None for row,outcome in classified):
            errors=sum(row['increase'] for row,outcome in classified if outcome)
            error_derivation=dict(state='pass',policy=ERROR_POLICY_VERSION,input_metric='servicegraph.request.count')
        else:
            errors=None
            error_derivation=dict(state='incomplete',reason='missing or unknown outcome request series')
    return dict(requests=count,errors=errors,duration_ms=histogram['value_ms'],histogram=histogram,partitions=dict(parts),series=detail,error_derivation=error_derivation,
                provenance=dict(state='fail' if wrong else ('pass' if series else 'incomplete'),wrong=wrong))


def raw_api_contract(samples, vm_rows, metric_name, source, thresholds):
    checks=[]
    for sample in selected(samples):
        labels=sample.get('labels',{})
        edge=sample.get('edge',{})
        if any(edge.get(field) is not None and labels.get(label)!=edge[field] for field,label in (('source_id','edge_client_id'),('target_id','edge_server_id'))):
            checks.append(dict(state='incomplete',reason='API edge identity conflicts with preserved labels')); continue
        if labels.get('source')!=source or sample.get('unit')!='count':
            checks.append(dict(state='fail',reason='raw API provenance or counter unit mismatch')); continue
        t=stamp(sample['timestamp']); candidates=[]; matched=[]
        derived=metric_name=='servicegraph_request_error_count' and source=='mesh.istio'
        input_name='servicegraph_request_count' if derived else metric_name
        if derived and (sample.get('derivation',{}).get('id')!='istio.request-errors' or str(sample.get('derivation',{}).get('version'))!='1' or sample.get('derivation',{}).get('input_metric')!='servicegraph.request.count'):
            checks.append(dict(state='incomplete',reason='historical mesh error sample lacks query derivation provenance')); continue
        for row in vm_rows:
            metric=row.get('metric',{})
            if metric.get('__name__')!=input_name or metric.get('source')!=source: continue
            if metric.get('entity_id')!=sample.get('entity_id'): continue
            if partition_key(metric)!=partition_key(labels): continue
            matched.append(row)
            candidates.extend((float(at)/1000,float(value)) for at,value in zip(row.get('timestamps',[]),row.get('values',[])) if float(at)/1000<=t)
        if not candidates:
            checks.append(dict(state='incomplete',reason='raw API has no matching stored full source series',labels=labels)); continue
        st=max(at for at,_ in candidates)
        values={value for at,value in candidates if at==st}
        if len(values)!=1:
            checks.append(dict(state='incomplete',reason='ambiguous stored values at source timestamp',labels=labels)); continue
        value=next(iter(values))
        if derived:
            outcome=request_failed(labels)
            if outcome is None:
                checks.append(dict(state='incomplete',reason='unknown outcome cannot derive error zero')); continue
            if not outcome: value=0.
        result=compare(value,float(sample['value']),0,0)
        result.update(timestamp=t,source_timestamp=st,lag_seconds=t-st,labels=labels)
        if t-st>thresholds['max_freshness_seconds']:
            result.update(state='incomplete',reason='raw stored sample stale')
        checks.append(result)
    return dict(state=state(checks),checks=checks)


def derivation_contract(samples, source):
    expected={'mesh.istio':('istio.request-errors','servicegraph.request.count'),
              'traces.servicegraph':('servicegraph.connector-failures','servicegraph.request.error.count')}.get(source)
    checks=[]
    for sample in selected(samples):
        meta=sample.get('derivation',{})
        valid=expected and meta.get('id')==expected[0] and str(meta.get('version'))=='1' and meta.get('input_metric')==expected[1] and bool(meta.get('interpretation'))
        checks.append(dict(state='pass' if valid else 'incomplete',metadata=meta,
                           reason=None if valid else 'missing or unsupported derivation contract'))
    return dict(state=state(checks),checks=checks)


def exact_query_parity(path, api_file, source):
    """Compare product-rendered query evidence at identical evaluation times."""
    document=read(path/'vm-parity'/api_file,{})
    samples=selected(api_samples(path,api_file))
    if not document:
        return dict(state='incomplete',reason='missing exact-query VM/API evidence',checks=[])
    points=[]
    responses=[document.get('response',{}),document.get('final',{}).get('response',{})]
    for response in responses:
        if response and response.get('status')!='success':
            return dict(state='incomplete',reason='VM exact query unsuccessful',checks=[])
        for row in response.get('data',{}).get('result',[]):
            values=row.get('values',[row['value']] if 'value' in row else [])
            for at,value in values:
                points.append((row.get('metric',{}),float(at),float(value)))
    checks=[]; missing_api=[]
    selected_points=[(metric,t,value) for metric,t,value in points if metric.get('source')==source and 'checkout' in metric.get('edge_server','') and 'frontend' in metric.get('edge_client','')]
    seen=set()
    for metric,t,value in selected_points:
        identity=(metric.get('entity_id'),partition_key(metric),t)
        if identity in seen: continue
        seen.add(identity)
        matches=[sample for sample in samples if sample.get('entity_id')==metric.get('entity_id') and partition_key(sample.get('labels',{}))==partition_key(metric) and abs(stamp(sample['timestamp'])-t)<.001]
        if not matches:
            detail=dict(entity_id=metric.get('entity_id'),labels=dimensions(metric),timestamp=t)
            missing_api.append(detail)
            checks.append(dict(state='incomplete',reason='stored exact-query partition/time absent from API',**detail))
    if not samples and not selected_points:
        checks.append(dict(state='incomplete',reason='no selected source evidence in either API or exact query'))
    for sample in samples:
        labels=sample.get('labels',{})
        if labels.get('source')!=source:
            checks.append(dict(state='fail',reason='wrong exact-query API source'));continue
        at=stamp(sample['timestamp'])
        candidates={value for metric,t,value in points if metric.get('entity_id')==sample.get('entity_id') and partition_key(metric)==partition_key(labels) and abs(t-at)<.001}
        if len(candidates)!=1:
            checks.append(dict(state='incomplete',reason='missing or ambiguous exact-query full partition/time',labels=labels,timestamp=at));continue
        result=compare(next(iter(candidates)),float(sample['value']),0,1e-9)
        result.update(labels=labels,timestamp=at)
        checks.append(result)
    return dict(state=state(checks),checks=checks,missing_api_partitions=missing_api,semantics='bidirectional same product-rendered query, full series identity and evaluation timestamp')


def quiet_boundaries(path):
    checks=[]
    for name in ('quiet-before.json','quiet-after.json'):
        evidence=read(path/name,{})
        checks.append(dict(boundary=name,state='pass' if evidence.get('stable') is True else 'incomplete',
                           reason=None if evidence.get('stable') is True else 'counter stability not established within bounded diagnostic',
                           elapsed_seconds=evidence.get('elapsed_seconds'),measured_collection_interval_seconds=evidence.get('measured_collection_interval_seconds')))
    return dict(state=state(checks),checks=checks,diagnostic_only=True)


def aligned_kubelet_reference(points,kind,target,cfg):
    choices=[]
    for captured,container in points:
        reading=container.get(kind,{})
        if reading.get('time') is None: continue
        source_time=stamp(reading['time'])
        cache_age=captured-source_time
        alignment=abs(source_time-target)
        valid=(-1<=cache_age<=cfg['max_alignment_seconds'] and alignment<=cfg['max_alignment_seconds'])
        choices.append((not valid,alignment,abs(cache_age),captured,source_time,reading))
    if not choices:
        return None,dict(state='incomplete',reason='missing kubelet underlying reading time')
    invalid,alignment,_,captured,source_time,reading=min(choices,key=lambda x:x[:3])
    detail=dict(state='incomplete' if invalid else 'pass',reference_timestamp=source_time,
                reference_captured_at=captured,kubelet_cache_age_seconds=captured-source_time,
                source_alignment_seconds=alignment)
    if invalid: detail['reason']='stale cached kubelet reading or underlying timestamp alignment exceeded'
    value=reading.get('usageNanoCores' if kind=='cpu' else 'usageBytes')
    if value is not None and kind=='cpu': value/=1e6
    return value,detail


def capture_integrity(path,source,demo=None):
    status=read(path/'capture-status.json',{})
    reasons=[]
    if not status.get('sampler_stopped'): reasons.append('missing capture completion or sampler still running')
    streams=status.get('streams',[])
    selected=[]
    for boundary in ('frontend','checkout'):
        matches=[x for x in streams if boundary in x.get('stream','') and x.get('stream','').endswith('__eval-observer')]
        if not matches: reasons.append('missing '+boundary+' observer stream lifecycle')
        selected.extend(matches)
    if source=='traces.servicegraph':
        source_names={'boutique':('eval-otlp-relay',),'astronomy':('otel-collector-agent',)}.get(demo,('eval-otlp-relay','otel-collector-agent'))
        matches=[x for x in streams if any(name in x.get('stream','') for name in source_names) and not x.get('stream','').endswith('__istio-proxy')]
        if not matches: reasons.append('missing selected trace source stream lifecycle: '+','.join(source_names))
    else:
        matches=[x for x in streams if 'checkout' in x.get('stream','') and x.get('stream','').endswith('__istio-proxy')]
        if not matches: reasons.append('missing selected checkout mesh stream lifecycle')
    selected.extend(matches)
    for stream in selected:
        if stream.get('exited_before_stop') or stream.get('error'):
            reasons.append('selected stream stopped early or failed: '+stream.get('stream','unknown'))
        stderr=path/'logs'/(stream.get('stream','')+'.stderr')
        if stderr.exists() and stderr.read_text().strip():
            reasons.append('selected log stream stderr: '+stream.get('stream','unknown'))
    if rows(path/'capture-errors.ndjson'): reasons.append('capture sampler errors present')
    if source=='mesh.istio' and any(x.get('error') for x in rows(path/'istio-samples.ndjson') if 'checkout' in x.get('pod','')):
        reasons.append('selected checkout mesh sampling errors present')
    return dict(valid=not reasons,state='pass' if not reasons else 'incomplete',reasons=reasons,selected_streams=selected)


def oracle_contract(terminals,attempted,conflicts,run_id_matches,capture_valid,source_valid,observer_boundaries):
    return bool(terminals and attempted and set(terminals)==set(attempted) and not conflicts
                and run_id_matches and capture_valid and source_valid and observer_boundaries)


def resource_scores(nodes,cpu,memory,summaries,namespace,start,end,thresholds,vm_rows=None,inventory=None):
    cfg={**DEFAULTS,**thresholds}; reference=defaultdict(list); overhead=defaultdict(set); overhead_points=defaultdict(list)
    in_window_reference_keys=set()
    lifecycle=container_lifecycle(inventory or [],start,end)
    not_applicable=[]
    def category(ns,pod,container):
        life=lifecycle.get((ns,pod,container),{})
        if not life.get('expected',True): return 'not_applicable'
        if life.get('category') in ('sidecar','init'): return life['category']
        if container in ('eval-observer','istio-proxy'): return container
        if any(name in (str(pod)+' '+str(container)).lower() for name in ('otel-collector','eval-otlp-relay','opentelemetrycollector')): return 'collectors_karthos'
        if any(s in pod.lower() for s in ('loadgenerator','locust','k6-','eval-k6','eval-locust')): return 'generator'
        if ns!=namespace: return 'collectors_karthos'
        return 'application'
    for row in summaries:
        t=stamp(row['captured_at'])
        # API latest may legitimately select a stored point from before arrival start.
        # Keep its independent reference history without expanding gauge alignment/freshness.
        reference_start=start-cfg['max_freshness_seconds']-cfg['max_alignment_seconds']
        if not reference_start<=t<=end+cfg['max_alignment_seconds']: continue
        for pod in row.get('summary',{}).get('pods',[]):
            ref=pod.get('podRef',{}); ns=ref.get('namespace'); name=ref.get('name')
            for c in pod.get('containers',[]):
                key=(ns,name,c['name']); cat=category(*key)
                if cat!='application':
                    if cat=='not_applicable': continue
                    if not start<=t<end: continue
                    overhead[cat].add(key)
                    overhead_points[(cat,key)].append((t,c))
                    continue
                reference[key].append((t,c))
                if start<=t<end: in_window_reference_keys.add(key)
    stored=defaultdict(list)
    for row in vm_rows or []:
        metric=row.get('metric',{})
        name=metric.get('__name__')
        kind={'k8s_container_cpu_usage':'cpu','k8s_container_memory_usage':'memory'}.get(name)
        if kind:
            for t,v in zip(row.get('timestamps',[]),row.get('values',[])):
                stored[(metric.get('entity_id'),kind)].append((float(t)/1000,float(v),metric))
    entities={}
    for n in nodes:
        if str(n.get('kind')).lower()!='container': continue
        labels=n.get('labels',{}); ns=n.get('namespace',labels.get('namespace',labels.get('k8s.namespace.name')))
        key=(ns,labels.get('k8s.pod.name'),labels.get('k8s.container.name',n.get('name')))
        if category(*key)=='application': entities[n['id']]=key
    grouped=defaultdict(list)
    for kind,samples,unit,absolute in [('cpu',cpu,'millicores',cfg['cpu_absolute_tolerance_millicores']),('memory',memory,'bytes',cfg['memory_absolute_tolerance_bytes'])]:
        for s in unique(samples):
            key=entities.get(s.get('entity_id'))
            if key is None: continue
            t=stamp(s.get('source_timestamp',s.get('observed_at',s.get('timestamp'))))
            if not start<=t<=end: continue
            item=dict(entity_id=s['entity_id'],container=list(key),timestamp=t,metric=kind)
            if s.get('labels',{}).get('source')!='kubelet' or s.get('unit')!=unit:
                item.update(state='fail',reason='wrong resource provenance or unit')
            elif not reference.get(key):
                item.update(state='incomplete',reason='missing kubelet container')
            else:
                value,alignment=aligned_kubelet_reference(reference[key],kind,t,cfg)
                item.update(compare(value,s.get('value'),cfg['resource_relative_tolerance'],absolute))
                if alignment['state']!='pass': item.update(alignment)
                else: item.update({k:v for k,v in alignment.items() if k!='state'})
            candidates=[x for x in stored[(s['entity_id'],kind)] if x[0]<=t and partition_key(x[2])==partition_key(s.get('labels',{}))]
            if candidates:
                latest_time=max(x[0] for x in candidates)
                if len({x[1] for x in candidates if x[0]==latest_time})!=1:
                    item.update(state='incomplete',reason='ambiguous stored resource values at source timestamp')
                    grouped[(key,kind)].append(item)
                    continue
                st,sv,metric=next(x for x in candidates if x[0]==latest_time)
                item['stored_timestamp']=st
                item['stored_lag_seconds']=t-st
                api_vm=compare(sv,s.get('value'),0,0)
                if t-st>cfg['max_freshness_seconds']:
                    api_vm=dict(state='incomplete',reason='stored sample stale')
                refs=reference.get(key,[])
                reference_vm=dict(state='incomplete',reason='no aligned source reference')
                if refs:
                    rv,alignment=aligned_kubelet_reference(refs,kind,st,cfg)
                    reference_vm=compare(rv,sv,cfg['resource_relative_tolerance'],absolute)
                    if alignment['state']!='pass': reference_vm.update(alignment)
                    else: reference_vm.update({k:v for k,v in alignment.items() if k!='state'})
                if metric.get('source')!='kubelet' or metric.get('karthos_unit')!=unit:
                    reference_vm=dict(state='fail',reason='wrong stored provenance or unit')
                item['reference_to_vm']=reference_vm
                item['vm_to_api']=api_vm
                # Direct evaluation-time comparison remains diagnostic only.
                item['evaluation_time_comparison']=dict(item)
                contract_failure=s.get('labels',{}).get('source')!='kubelet' or s.get('unit')!=unit
                item['state']='fail' if contract_failure else state([reference_vm,api_vm])
            else:
                item['reference_to_vm']=dict(state='incomplete',reason='missing VM resource sample')
                item['vm_to_api']=dict(state='incomplete',reason='missing VM resource sample')
                if item['state']!='fail': item['state']='incomplete'
            grouped[(key,kind)].append(item)
    not_applicable=[dict(container=list(key),**value) for key,value in lifecycle.items() if not value['expected']]
    checks=[]
    expected_keys=in_window_reference_keys|set(entities.values())
    for key in expected_keys:
        for kind in ('cpu','memory'):
            checks.extend(grouped[(key,kind)] or [dict(container=list(key),metric=kind,state='incomplete',reason='missing API container samples')])
    overhead_metrics=[]
    for (cat,key),points in overhead_points.items():
        cpu_values=[c['cpu']['usageNanoCores']/1e6 for _,c in points if c.get('cpu',{}).get('usageNanoCores') is not None]
        mem_values=[c['memory']['usageBytes'] for _,c in points if c.get('memory',{}).get('usageBytes') is not None]
        overhead_metrics.append(dict(category=cat,container=list(key),samples=len(points),
                                     cpu_millicores_mean=sum(cpu_values)/len(cpu_values) if cpu_values else None,
                                     cpu_millicores_max=max(cpu_values) if cpu_values else None,
                                     memory_usage_bytes_mean=sum(mem_values)/len(mem_values) if mem_values else None,
                                     memory_usage_bytes_max=max(mem_values) if mem_values else None))
    return dict(state=state(checks),overhead_metrics=overhead_metrics,expected_application_containers=len(expected_keys),not_applicable=not_applicable,container_lifecycle=[dict(container=list(k),**v) for k,v in lifecycle.items()],
                checks=checks,overhead={k:[list(x) for x in sorted(v)] for k,v in overhead.items()},
                reference_contract='CPU usageNanoCores / 1e6 millicores; memory usageBytes, not workingSetBytes')


def observer_records(path,start,end):
    result=[]
    for file in (path/'logs').glob('*eval-observer.log'):
        for line in file.read_text(errors='replace').splitlines():
            pos=line.find('{')
            if pos<0: continue
            try: obj=json.loads(line[pos:])
            except ValueError: continue
            if 'observed_at' in obj and start<=stamp(obj['observed_at'])<end:
                result.append(obj)
    return unique(result)


def graph_scores(path,namespace):
    from .artifacts import evaluate_entities, evaluate_runs_on
    if not all((path/name).exists() for name in ('k8s-pods.json','k8s-services.json','api-nodes.json','api-edges.json')):
        missing=dict(state='incomplete',reason='missing Kubernetes or canonical graph inventory')
        return dict(entities=missing,runs_on=missing)
    entities=evaluate_entities(path,namespace)
    entities['state']='incomplete' if not entities['expected'] or entities['missing'] else 'pass'
    runs_on=evaluate_runs_on(path,namespace)
    runs_on['state']='incomplete' if not runs_on['expected'] or runs_on['missing'] or runs_on['unresolved_entities'] else 'pass'
    return dict(entities=entities,runs_on=runs_on)


def generator_artifact_completeness(path,manifest):
    """Opt-in raw export verification, repeated on every offline evaluation."""
    if manifest.get('artifact_protocol')!=1:
        return None
    reasons=[]
    try:
        saved=read(path/'generator-artifact-status.json',{})
        if not isinstance(saved,dict) or saved.get('verified') is not True or saved.get('semantic_complete') is not True:
            reasons.append('missing or incomplete saved generator artifact status')
    except (ValueError,OSError) as exc:
        saved={}
        reasons.append('unusable saved generator artifact status: '+str(exc))
    verified={}
    workload_exit_code=None
    try:
        from .generator_artifacts import validate_export
        verified=validate_export(path/'generator-artifacts',manifest.get('generator'),
                                 manifest.get('generator_evidence_mode',manifest.get('mode')),manifest.get('run_id'))
        if not isinstance(verified,dict) or verified.get('verified') is not True or verified.get('semantic_complete') is not True:
            reasons.append('raw generator artifact verification incomplete')
        else:
            workload_exit_code=verified.get('workload_exit_code')
            if workload_exit_code is None:
                workload_exit_code=int((path/'generator-artifacts/workload-exit-code.txt').read_text().strip())
            if not isinstance(workload_exit_code,int) or not 0<=workload_exit_code<=255:
                reasons.append('invalid verified workload exit code')
    except (ImportError,ValueError,OSError,TypeError) as exc:
        reasons.append('raw generator artifact verification failed: '+str(exc))
    return dict(valid=not reasons,state='pass' if not reasons else 'incomplete',
                reasons=reasons,saved_status=saved,reverified_status=verified,
                workload_exit_code=workload_exit_code)


def incomplete_window(path,manifest,reason):
    return dict(protocol_version=PROTOCOL_VERSION,verifier_version=VERIFIER_VERSION,window=str(path),manifest=manifest,state='incomplete',oracle_valid=False,
                source_valid=False,descriptive_only=manifest.get('mode')=='native',
                generator={'requests':0,'unexpected_outcomes':0},source={'requests':None,'errors':None},
                counts_path={},resources={'state':'incomplete','overhead_metrics':[]},duration={},
                stage_table=[dict(stage='artifact_read',state='incomplete',reason=reason)],reasons=[reason])


def evaluate_window(path: Path, thresholds: dict, *, protocol_version=None)->dict:
    try:
        manifest=read(Path(path)/'manifest.json',{})
        if (protocol_version if protocol_version is not None else manifest.get('protocol_version',0))>=4:
            from .verification_v4 import evaluate_window as evaluate_v4
            return evaluate_v4(Path(path),thresholds)
        return _evaluate_window(Path(path),thresholds)
    except (ValueError,KeyError,TypeError,OSError) as exc:
        try: manifest=read(Path(path)/'manifest.json',{})
        except (ValueError,OSError): manifest={}
        return incomplete_window(path,manifest,'unusable artifact: '+str(exc))


def _evaluate_window(path: Path, thresholds: dict, *, resource_provider=None, trace_provider=None)->dict:
    resource_provider = resource_provider or resource_scores
    trace_provider = trace_provider or trace_source
    path=Path(path); cfg={**DEFAULTS,**thresholds}; manifest=read(path/'manifest.json',{})
    if not manifest.get('start') and manifest.get('start')!=0 or 'end' not in manifest:
        return incomplete_window(path,manifest,'missing manifest bounds')
    start,end=stamp(manifest['start']),stamp(manifest['end']); source_id=manifest.get('source_id','')
    native=manifest.get('mode')=='native'
    artifact_gate=generator_artifact_completeness(path,manifest)
    markers=read(path/'generator-window.json',[])
    final_marker=next((x for x in reversed(markers) if x.get('ended_at')),markers[-1] if markers else {})
    effective_run_id=final_marker.get('run_id',manifest.get('run_id'))
    raw_ledger=rows(path/'ledger.ndjson'); raw_attempts=rows(path/'attempts.ndjson')
    ledger=unique(raw_ledger); attempts=unique(raw_attempts)
    ledger=[x for x in ledger if x.get('run_id')==effective_run_id and start<=stamp(x['started_at'])<end]
    attempts=[x for x in attempts if x.get('run_id')==effective_run_id and start<=stamp(x['started_at'])<end]
    conflicts=len(ledger)-len({x['request_id'] for x in ledger}) + len(attempts)-len({x['request_id'] for x in attempts})
    terminals={x['request_id']:x for x in ledger}; attempted={x['request_id']:x for x in attempts}
    generator=dict(requests=len(terminals),attempts=len(attempted),
                   duplicate_ledger_records=len(raw_ledger)-len(unique(raw_ledger)),
                   duplicate_attempt_records=len(raw_attempts)-len(unique(raw_attempts)),
                   conflicting_request_ids=conflicts,
                   unterminated_attempts=len(set(attempted)-set(terminals)),unexpected_outcomes=sum(not x.get('outcome_ok',False) for x in terminals.values()),
                   routes=dict(Counter(x.get('route','unknown') for x in terminals.values())),
                   statuses=dict(Counter(str(x.get('status',0)) for x in terminals.values())),job_failed=bool(manifest.get('generator_job_failed')))
    if artifact_gate is not None:
        generator['workload_exit_code']=artifact_gate['workload_exit_code']
        # Held Kubernetes Job termination is independent of the workload result.
        generator['job_failed']=artifact_gate['workload_exit_code'] not in (None,0)
    from .verify import k6_quality
    generator['dropped_iterations']=k6_quality(path/'k6.ndjson')['dropped_iterations'] if (path/'k6.ndjson').exists() else None
    generator['effective_run_id']=effective_run_id
    generator['manifest_run_id_matches']=manifest.get('run_id')==effective_run_id
    generator['expected_iterations']=final_marker.get('expected_iterations')
    generator['actual_iterations_started']=len({x.get('journey_id') for x in attempts})
    generator['completed_requests']=sum(bool(x.get('completed',int(x.get('status',0))>0)) for x in terminals.values())
    workload=read(Path(__file__).parent/'workloads/workload.json',{})
    schedule=workload.get('workloads',{}).get(manifest.get('workload',''),{}).get('schedule',[])
    journeys=workload.get('demos',{}).get(manifest.get('demo',''),{}).get('journeys',{})
    completed_iterations=0
    for iteration in {x.get('iteration') for x in ledger if x.get('iteration') is not None}:
        members=[x for x in ledger if x.get('iteration')==iteration]
        if schedule:
            length=len(journeys.get(schedule[int(iteration)%len(schedule)],[]))
            completed_iterations+=bool(length and {x.get('step_index') for x in members}==set(range(length)))
    generator['actual_iterations_completed']=completed_iterations if schedule else None
    generator['schedule_shortfall_iterations']=max(0,float(generator['expected_iterations'])-generator['actual_iterations_started']) if generator['expected_iterations'] is not None else None
    observations=observer_records(path,start,end); frontend=[x for x in observations if x.get('boundary')=='frontend' and x.get('run_id')==effective_run_id]
    checkout=[x for x in observations if x.get('boundary')=='checkout' and x.get('uri','').endswith('/PlaceOrder')]
    joined={x.get('request_id'):x for x in frontend}
    frontend_checks=[]
    for rid,req in terminals.items():
        actual=joined.get(rid)
        frontend_checks.append(dict(request_id=rid,route=req.get('route'),generator_status=req.get('status'),
                                    observer_status=actual.get('status') if actual else None,
                                    state='incomplete' if not actual else ('pass' if int(actual['status'])==int(req['status']) else 'fail')))
    frontend_result=dict(state=state(frontend_checks),checks=frontend_checks,observed=len(frontend),
                         extra_request_ids=sorted(set(joined)-set(terminals)))
    source=mesh_source(path,start,end,cfg) if source_id=='mesh.istio' else trace_provider(path,start,end)
    vm=vm_source(path,start,end,source_id,cfg)
    api_request=api_count(api_samples(path,'api-request-count.json'),source_id)
    api_error=api_count(api_samples(path,'api-error-count.json'),source_id)
    def count_check(a,b): return compare(a,b,cfg['request_relative_tolerance'],cfg['request_absolute_tolerance'])
    observer_outcomes=[request_failed(dict(x,grpc_response_status=x.get('grpc_status'),request_protocol='grpc')) for x in checkout]
    observer_errors=sum(x is True for x in observer_outcomes) if all(x is not None for x in observer_outcomes) else None
    counts=dict(raw_request_api=raw_api_contract(api_samples(path,'api-request-raw.json'),rows(path/'vm-raw.ndjson'),'servicegraph_request_count',source_id,cfg),
                raw_error_api=raw_api_contract(api_samples(path,'api-error-raw.json'),rows(path/'vm-raw.ndjson'),'servicegraph_request_error_count',source_id,cfg),
                observer_to_source=count_check(len(checkout) if checkout else None,source['requests']),
                observer_errors_to_source=count_check(observer_errors if checkout else None,source['errors']),
                source_to_vm=count_check(source['requests'],vm['requests']),
                source_errors_to_vm=count_check(source['errors'],vm['errors']),
                vm_to_api=count_check(vm['requests'],api_request['value']),
                vm_errors_to_api=count_check(vm['errors'],api_error['value']),
                api_request_contract=api_request,api_error_contract=api_error,vm_provenance=vm['provenance'])
    for key in ('vm_to_api','vm_errors_to_api'):
        counts[key]['diagnostic_only']=True
        counts[key]['semantics']='workload-window bracketing increase versus API query window; not exact storage/API parity'
    for name in ('request-count','error-count','duration'):
        counts['exact_'+name.replace('-','_')+'_api']=exact_query_parity(path,'api-'+name+'.json',source_id)
    counts['quiet_boundaries']=quiet_boundaries(path)
    counts['error_derivation_contract']=derivation_contract(api_samples(path,'api-error-count.json'),source_id)
    duration_samples=selected(api_samples(path,'api-duration.json'))
    weights=selected(api_samples(path,'api-request-count.json'))
    api_duration=weighted_duration(duration_samples,weights)
    if any(s.get('labels',{}).get('source')!=source_id for s in duration_samples):
        api_duration.update(state='fail',value_ms=None,reason='wrong duration provenance')
    proxy_values=[]
    for x in checkout:
        try: proxy_values.append(float(x['request_time_seconds'])*1000)
        except (KeyError,ValueError,TypeError): pass
    proxy_mean=sum(proxy_values)/len(proxy_values) if proxy_values else None
    def dur_check(a,b): return compare(a,b,cfg['duration_relative_tolerance'],cfg['duration_absolute_tolerance_ms'])
    duration=dict(api=api_duration,source_to_vm=dur_check(source['duration_ms'],vm['duration_ms']),vm_to_api=dur_check(vm['duration_ms'],api_duration['value_ms']),source_to_api=dur_check(source['duration_ms'],api_duration['value_ms']),
                  proxy_to_api=dur_check(proxy_mean,api_duration['value_ms']),
                  proxy_timing_semantics='independent checkout proxy duration includes network/proxy overhead, not exact application time',
                  vm_histogram_mean_ms=vm['duration_ms'])
    nodes=read(path/'api-nodes.json',{}).get('data',{}).get('nodes',[])
    known={(n.get('namespace'),n.get('labels',{}).get('k8s.pod.name'),n.get('labels',{}).get('k8s.container.name')) for n in nodes if n.get('kind')=='container'}
    for pod in read(path/'k8s-pods.json',{}).get('items',[]):
        meta=pod['metadata']; ns=meta.get('namespace',manifest.get('namespace'))
        for c in pod.get('spec',{}).get('containers',[])+pod.get('spec',{}).get('initContainers',[]):
            key=(ns,meta['name'],c['name'])
            if key not in known:
                nodes.append(dict(id='missing-canonical-entity:'+':'.join(key),kind='container',namespace=ns,
                                  labels={'k8s.pod.name':meta['name'],'k8s.container.name':c['name']},inventory_only=True))
    resources=resource_provider(nodes,
                              api_samples(path,'api-cpu.json'),api_samples(path,'api-memory.json'),
                              rows(path/'kubelet.ndjson'),manifest.get('namespace'),start,end,cfg,rows(path/'vm-raw.ndjson'),read(path/'k8s-pods.json',{}).get('items',[]))
    capture=capture_integrity(path,source_id,demo=manifest.get('demo'))
    oracle=oracle_contract(terminals,attempted,conflicts,generator['manifest_run_id_matches'],capture['valid'] and not manifest.get('interrupted') and manifest.get('phase')!='incomplete' and bool(final_marker.get('ended_at')),source['valid'],bool(frontend and checkout))
    if artifact_gate is not None: oracle=oracle and artifact_gate['valid']
    reasons=list(capture['reasons'])
    if artifact_gate is not None: reasons.extend(artifact_gate['reasons'])
    if manifest.get('interrupted') or manifest.get('phase')=='incomplete': reasons.append('generator observation interrupted')
    if not final_marker.get('ended_at'): reasons.append('missing final generator window marker')
    if not generator['manifest_run_id_matches']: reasons.append('generator marker run ID differs from manifest')
    if set(terminals)-set(attempted): reasons.append('terminal requests missing pre-network attempts')
    if not frontend or not checkout: reasons.append('missing independent frontend or checkout observer evidence')
    if conflicts: reasons.append('conflicting records for request IDs')
    if (generator['dropped_iterations'] or 0)>0: reasons.append('k6 dropped scheduled iterations')
    if manifest.get('generator')=='k6' and generator['dropped_iterations'] is None: reasons.append('missing k6 granular quality evidence')
    if not terminals: reasons.append('missing terminal generator ledger')
    if not attempted: reasons.append('missing pre-network attempt ledger')
    if generator['unterminated_attempts']: reasons.append('unterminated generator attempts')
    if not source['valid']: reasons.append('no usable checkout raw source evidence')
    graph=graph_scores(path,manifest.get('namespace'))
    quality_state='fail' if generator['unexpected_outcomes'] or generator['job_failed'] or (generator['dropped_iterations'] or 0)>0 else ('pass' if oracle and not (manifest.get('generator')=='k6' and generator['dropped_iterations'] is None) else 'incomplete')
    checks=[dict(state=quality_state),frontend_result,resources,*graph.values(),*[v for v in counts.values() if not v.get('diagnostic_only')],api_duration,vm['histogram'],duration['source_to_vm'],capture]
    if artifact_gate is not None: checks.append(artifact_gate)
    if source.get('unpaired'): checks.append(dict(state='incomplete')); reasons.append('unpaired checkout SERVER spans')
    overall=state(checks)
    if not oracle and overall=='pass': overall='incomplete'
    stage_table=[*[dict(stage=k,**v) for k,v in graph.items()],dict(stage='generator_to_frontend',state=frontend_result['state'],expected=len(terminals),observed=len(frontend)),
                 *[dict(stage=k,**v) for k,v in counts.items()],
                 dict(stage='source_duration_to_vm',**duration['source_to_vm']),dict(stage='vm_duration_to_api',diagnostic_only=True,**duration['vm_to_api']),dict(stage='capture_integrity',**capture),dict(stage='source_duration_to_api',diagnostic_only=True,**duration['source_to_api']),
                 dict(stage='independent_proxy_duration_to_api',diagnostic_only=True,**duration['proxy_to_api']),
                 dict(stage='application_cpu_memory',state=resources['state'],expected_containers=resources['expected_application_containers'])]
    if artifact_gate is not None: stage_table.append(dict(stage='generator_artifact_completeness',**artifact_gate))
    native_files=('generator.log','generator-version.txt','locust-stats.csv','locust-report.html',
                  'api-nodes.json','api-cpu.json','api-memory.json','kubelet.ndjson')
    native_missing=[name for name in native_files if not evidence_path(path/name).exists() or not evidence_path(path/name).stat().st_size] if native else []
    native_failure_count=None
    native_request_count=None
    if native and (path/'locust-stats.csv').exists():
        import csv
        for row in csv.DictReader((path/'locust-stats.csv').read_text().splitlines()):
            if row.get('Name')=='Aggregated':
                try:
                    native_failure_count=int(row['Failure Count'])
                    native_request_count=int(row['Request Count'])
                except (KeyError,ValueError):
                    pass
    native_evidence=dict(complete=bool(native and not native_missing and capture['valid'] and not generator['job_failed'] and (artifact_gate is None or artifact_gate['valid']) and not manifest.get('interrupted') and not manifest.get('native_boundary_error') and manifest.get('phase')!='incomplete'),
                         execution_failed=generator['job_failed'],
                         locust_request_failures=native_failure_count,locust_requests=native_request_count,
                         request_failure_semantics='Locust aggregate failures can include HTTP and transport failures; independent of Kubernetes job execution status.',
                         missing_artifacts=native_missing)
    return dict(protocol_version=PROTOCOL_VERSION,verifier_version=VERIFIER_VERSION,error_policy_version=ERROR_POLICY_VERSION,native_evidence=native_evidence,stage_table=stage_table,window=str(path),manifest=manifest,state='incomplete' if native else overall,
                descriptive_only=native,oracle_valid=oracle and not native,source_valid=bool(source['valid']),
                capture=capture,generator_artifacts=artifact_gate,generator=generator,frontend=frontend_result,entities=graph['entities'],runs_on_edges=graph['runs_on'],counts_path=counts,source=source,vm=vm,
                resources=resources,duration=duration,reasons=reasons,
                limitations=['Counter boundary slack is explicit and not extrapolated.',
                             'Missing stages remain unknown; no loss attribution is inferred.',
                             'Checkout proxy records lack propagated request IDs; checkout is a time-bounded aggregate.'])


def aggregate_study(run_dir: Path)->dict:
    run_dir=Path(run_dir); thresholds=read(run_dir/'frozen-thresholds.json',{})
    reports=[]
    for p in sorted((run_dir/'measured').glob('*/windows/*/manifest.json')):
        reports.append(evaluate_window(p.parent,thresholds))
    matched=[x for x in reports if not x['descriptive_only']]; native=[x for x in reports if x['descriptive_only']]
    expected_keys={(f'r{repeat}-{demo}-{source}',generator,workload)
                   for repeat in (1,2,3) for demo in ('boutique','astronomy') for source in ('trace','mesh')
                   for generator in ('k6','locust') for workload in ('legacy','checkout-heavy')}
    actual_keys={(x['manifest'].get('cell'),x['manifest'].get('generator'),
                  x['manifest'].get('workload',Path(x['window']).name.split('-',1)[-1])) for x in matched}
    missing_keys=sorted(expected_keys-actual_keys)
    expected=48
    status=Counter(x['state'] for x in matched)
    absent=len(missing_keys); status['incomplete']+=absent
    expected_native={f'r1-{demo}-{source}' for demo in ('boutique','astronomy') for source in ('trace','mesh')}
    present_native={x['manifest'].get('cell') for x in native}
    missing_native=sorted(expected_native-present_native)
    native_incomplete=[x.get('window') for x in native if not x.get('native_evidence',{}).get('complete')]
    required_incomplete=bool(missing_keys or missing_native or native_incomplete)
    comparison=defaultdict(list)
    for result in matched:
        m=result['manifest']
        comparison[(m.get('demo'),m.get('source'),m.get('workload'))].append(dict(
            repeat=m.get('repeat'),generator=m.get('generator'),state=result['state'],
            generator_quality=result['generator'],source_requests=result['source']['requests'],
            source_errors=result['source']['errors'],counts_path=result['counts_path'],
            generator_footprint=[x for x in result['resources']['overhead_metrics'] if x['category']=='generator']))
    return dict(protocol_version=2,generator_comparison=[dict(demo=d,source=s,workload=w,runs=runs) for (d,s,w),runs in comparison.items()],state='fail' if status['fail'] else ('incomplete' if status['incomplete'] or required_incomplete else 'pass'),
                expected_matched_windows=expected,attempted_matched_windows=len(matched),absent_matched_windows=absent,
                missing_matched_window_ids=[dict(cell=c,generator=g,workload=w) for c,g,w in missing_keys],
                matched_states=dict(status),windows=matched,native_descriptive_windows=native,expected_native_windows=4,absent_native_windows=len(missing_native),missing_native_window_ids=missing_native,native_incomplete_windows=native_incomplete,
                source_pass_matrix=[dict(cell=x['manifest'].get('cell'),generator=x['manifest'].get('generator'),
                                         run_id=x['manifest'].get('run_id'),source_valid=x['source_valid'],state=x['state']) for x in matched],
                failed_attempts=[str(x.relative_to(run_dir)) for x in sorted((run_dir/'failed-attempts').glob('**/status.json'))])
