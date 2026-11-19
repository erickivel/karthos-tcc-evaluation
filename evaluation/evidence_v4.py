"""Structured evidence providers for protocol4; no nearest-value inference."""
from __future__ import annotations

import gzip
import json
import math
from collections import defaultdict
from pathlib import Path

from .series_identity import partition_key


def stamp(value):
    from .scoring import stamp as parse
    return parse(value)


def attrs(values):
    return {entry['key']: next(iter(entry.get('value', {}).values()), None) for entry in values}


def documents(directory):
    for file in sorted(Path(directory).glob('*')):
        if not file.is_file() or not (file.name.endswith('.json') or file.name.endswith('.json.gz')):
            continue
        opener = gzip.open if file.name.endswith('.gz') else open
        with opener(file, 'rt') as source:
            for line in source:
                if line.strip():
                    yield json.loads(line)


def raw_metrics(directory):
    for doc in documents(directory):
        for group in doc.get('resourceMetrics', []):
            resource = attrs(group.get('resource', {}).get('attributes', []))
            for scope in group.get('scopeMetrics', []):
                for metric in scope.get('metrics', []):
                    for kind in ('gauge', 'sum', 'histogram'):
                        for point in metric.get(kind, {}).get('dataPoints', []):
                            yield resource, metric, kind, attrs(point.get('attributes', [])), point


def raw_spans(directory):
    for doc in documents(directory):
        for group in doc.get('resourceSpans', []):
            resource = attrs(group.get('resource', {}).get('attributes', []))
            for scope in group.get('scopeSpans', []):
                for span in scope.get('spans', []):
                    kind = {2:'server', 3:'client', 'SPAN_KIND_SERVER':'server', 'SPAN_KIND_CLIENT':'client'}.get(span.get('kind'), 'other')
                    yield dict(trace_id=span['traceId'], span_id=span['spanId'],
                         parent_id=span.get('parentSpanId'), service=resource.get('service.name'),
                         name=span.get('name', ''), kind=kind,
                         start=int(span['startTimeUnixNano'])/1e9,
                         end=int(span['endTimeUnixNano'])/1e9,
                         failed=span.get('status', {}).get('code') in (2, 'STATUS_CODE_ERROR'),
                         attributes=attrs(span.get('attributes', [])))


GRPC_STATUS_NAMES = ('OK','CANCELLED','UNKNOWN','INVALID_ARGUMENT','DEADLINE_EXCEEDED',
                     'NOT_FOUND','ALREADY_EXISTS','PERMISSION_DENIED','RESOURCE_EXHAUSTED',
                     'FAILED_PRECONDITION','ABORTED','OUT_OF_RANGE','UNIMPLEMENTED',
                     'INTERNAL','UNAVAILABLE','DATA_LOSS','UNAUTHENTICATED')


def grpc_outcome(attributes):
    """Preserve legacy/stable observations; diagnostic outcome is not span status."""
    keys=('rpc.system','rpc.system.name','rpc.grpc.status_code','rpc.response.status_code')
    observed={k:attributes[k] for k in keys if k in attributes}
    result=dict(grpc_status=attributes.get('rpc.grpc.status_code',attributes.get('rpc.response.status_code')),
                grpc_status_attributes=observed,grpc_status_code=None,grpc_status_state='missing',grpc_outcome_failed=None)
    systems={attributes[k] for k in ('rpc.system','rpc.system.name') if isinstance(attributes.get(k),str)}
    if systems and systems!={'grpc'}:
        result['grpc_status_state']='non-grpc or conflicting protocol';return result
    codes=[];invalid=False
    if 'rpc.grpc.status_code' in attributes:
        value=attributes['rpc.grpc.status_code']
        if isinstance(value,int) and not isinstance(value,bool) and 0<=value<len(GRPC_STATUS_NAMES):codes.append(value)
        elif isinstance(value,str) and value.isascii() and value.isdigit() and int(value)<len(GRPC_STATUS_NAMES):codes.append(int(value))
        else:invalid=True
    if 'rpc.response.status_code' in attributes:
        value=attributes['rpc.response.status_code']
        if systems=={'grpc'} and isinstance(value,str) and value in GRPC_STATUS_NAMES:codes.append(GRPC_STATUS_NAMES.index(value))
        else:invalid=True
    if len(set(codes))>1:
        result['grpc_status_state']='conflict'
    elif invalid:
        result['grpc_status_state']='unknown'
    elif codes:
        result.update(grpc_status_code=codes[0],grpc_status_state='observed',grpc_outcome_failed=codes[0]!=0)
    return result


def trace_accounting(spans, start, end):
    identities = {}; conflicts = set()
    for span in spans:
        key = span['trace_id'], span['span_id']
        if key in identities and identities[key] != span:
            conflicts.add(key)
        identities[key] = span
    selected = []
    for span in identities.values():
        if span['kind'] != 'server' or span['service'] not in ('checkout','checkoutservice') or not span.get('name','').endswith('/PlaceOrder'):
            continue
        if not start <= span['start'] < end:
            continue
        parent = identities.get((span['trace_id'], span.get('parent_id')))
        paired = bool(parent and parent['kind'] == 'client' and parent['service'] == 'frontend')
        selected.append(dict(trace_id=span['trace_id'], span_id=span['span_id'],
            parent_id=span.get('parent_id'), paired_frontend=paired,
            failed=span['failed'] or bool(paired and parent['failed']),
            client_span_status='Error' if paired and parent['failed'] else ('Unset' if paired else None),
            server_span_status='Error' if span['failed'] else 'Unset',
            **grpc_outcome(span['attributes']),
            duration_ms=(span['end']-span['start'])*1000))
    paired = [s for s in selected if s['paired_frontend']]
    return dict(valid=bool(paired) and not conflicts, requests=len(paired) if selected else None,
        errors=sum(s['failed'] for s in paired) if selected else None,
        duration_ms=sum(s['duration_ms'] for s in paired)/len(paired) if paired else None,
        unpaired=len(selected)-len(paired), spans=selected, conflicting_spans=len(conflicts),
        grpc_outcome_errors=sum(s['grpc_outcome_failed'] is True for s in paired),
        grpc_outcome_unknown=sum(s['grpc_outcome_failed'] is None for s in paired),
        error_semantics='connector-native client OR server OTel span ERROR; gRPC status retained separately',
        duration_semantics='selected paired checkout SERVER spans; raw structured gateway ingress')


def capture_component_valid(path, key):
    """Acquisition failure excludes parser input, never changes evidence validity."""
    from .scoring import read
    status=read(Path(path)/'structured/status.json',{})
    component=status.get('components',{}).get(key,{})
    frozen=component.get('finalization',status.get('finalization',{}).get('components',{}).get(key,{}))
    return not (component.get('errors') or component.get('complete') is False or
                frozen.get('errors') or frozen.get('complete') is False or
                any(row.get('complete') is False for row in frozen.get('files',{}).values()))


def trace_source(path, start, end):
    if not capture_component_valid(path,'gateway'):
        return dict(state='incomplete',valid=False,reason='Gateway capture incomplete; raw records excluded from scoring',requests=None,errors=None,duration_ms=None)
    return trace_accounting(raw_spans(Path(path)/'structured/gateway/raw'), start, end)


def container_reading(body, point):
    if body.get('node', {}).get('nodeName') != point.get('node'):
        return None
    for pod in body.get('pods', []):
        ref = pod.get('podRef', {})
        if (ref.get('namespace'), ref.get('name'), ref.get('uid')) != (point['namespace'],point['pod'],point['pod_uid']):
            continue
        for container in pod.get('containers', []):
            if container.get('name') == point['container']:
                return container.get(point['kind'])
    return None


def canonical_reading(reading, kind, cpu_unit='millicores'):
    field = 'usageNanoCores' if kind == 'cpu' else 'usageBytes'
    value = reading.get(field)
    return None if value is None else float(value)/((1e9 if cpu_unit=='cores' else 1e6) if kind=='cpu' else 1)


def measurement_time_ns(value):
    """Exact kubelet measurement identity; acquisition-time slack is separate."""
    from decimal import Decimal
    from .log_capture import timestamp_ns
    if isinstance(value,str):
        return timestamp_ns(value)
    if isinstance(value,bool) or not isinstance(value,(int,float,Decimal)):
        raise ValueError('measurement timestamp must be RFC3339Nano or numeric seconds')
    nanoseconds=Decimal(str(value))*1_000_000_000
    if not nanoseconds.is_finite() or nanoseconds!=nanoseconds.to_integral_value():
        raise ValueError('measurement timestamp must have nanosecond precision')
    return int(nanoseconds)


def check_resource_input(point, inputs, independent, thresholds):
    gap = thresholds.get('max_alignment_seconds', 10)
    matches = []
    for record in inputs:
        if record.get('status') != 200 or record.get('path') != '/stats/summary' or not point.get('collector_uid') or record.get('collector_uid') != point['collector_uid']:
            continue
        before, after = record['requested_at_ns']/1e9, record['received_at_ns']/1e9
        if after < before or not before-gap <= point['timestamp'] <= after+gap:
            continue
        reading = container_reading(record.get('body', {}), point)
        if reading is not None:
            matches.append((record, reading))
    result = dict(conversion={'state':'incomplete','reason':'missing exact receiver response'},
                  independent_agreement={'state':'incomplete','reason':'no unique input measurement'},
                  canonical_value=None)
    if len(matches) != 1:
        if matches:
            result['conversion']['reason'] = 'ambiguous receiver responses; no candidate chosen'
        return result
    record, reading = matches[0]
    cpu_unit=point.get('canonical_unit','millicores')
    value = canonical_reading(reading, point['kind'],cpu_unit)
    expected_unit = '{cpu}' if point['kind'] == 'cpu' else 'By'
    canonical = point['value']*(1000 if point['kind']=='cpu' and cpu_unit=='millicores' else 1)
    result.update(canonical_value=canonical, reference_value=value,
                  input_requested_at=record['requested_at_ns']/1e9,
                  input_received_at=record['received_at_ns']/1e9)
    if point.get('source') != 'kubelet' or point.get('unit') != expected_unit:
        result['conversion'] = {'state':'fail','reason':'wrong raw resource source or unit'}
    elif value is None:
        result['conversion'] = {'state':'incomplete','reason':'receiver measurement field is missing'}
    else:
        result['conversion'] = dict(state='pass' if math.isclose(value,canonical,rel_tol=1e-12,abs_tol=1e-8) else 'fail', reference=value,observed=canonical)
    if reading.get('time') is None:
        result['independent_agreement']['reason'] = 'missing underlying kubelet measurement time'
        return result
    try:
        measurement_ns = measurement_time_ns(reading['time'])
    except (ValueError,TypeError):
        result['independent_agreement']['reason'] = 'invalid underlying kubelet measurement time'
        return result
    measurement = measurement_ns/1e9
    result.update(measurement_timestamp=measurement, measurement_timestamp_ns=measurement_ns,
                  measurement_serialized_timestamp=reading['time'],
                  cache_age_seconds=result['input_received_at']-measurement,
                  acquisition_alignment_seconds=abs(point['timestamp']-result['input_received_at']))
    candidates = {}
    for observation in independent:
        ref = container_reading(observation.get('summary', {}), point)
        if ref is None or ref.get('time') is None:
            continue
        try:
            if measurement_time_ns(ref['time']) != measurement_ns:
                continue
        except (ValueError,TypeError):
            continue
        reference = canonical_reading(ref,point['kind'],cpu_unit)
        if reference is not None:
            candidates[reference] = observation.get('received_at', observation.get('captured_at'))
    if len(candidates) != 1:
        result['independent_agreement'] = dict(state='incomplete', reason='missing same-measurement independent reference' if not candidates else 'ambiguous values for same independent measurement')
    else:
        reference, captured = next(iter(candidates.items()))
        from .scoring import compare
        absolute = thresholds.get('cpu_absolute_tolerance_millicores',10) if point['kind']=='cpu' else thresholds.get('memory_absolute_tolerance_bytes',1048576)
        if point['kind']=='cpu' and cpu_unit=='cores':absolute/=1000
        result['independent_agreement'] = dict(compare(reference,canonical,thresholds.get('resource_relative_tolerance',.2),absolute),
             measurement_timestamp=measurement, measurement_timestamp_ns=measurement_ns, captured_at=captured)
    return result


def vm_time_identity(value):
    """Round query evaluation time to VM's integer millisecond grid.

    Parse RFC3339Nano before datetime/float conversion so serialization tails
    remain auditable. This selects timestamps, never nearest numeric values.
    Raw OTLP nanoseconds and underlying kubelet measurement times stay separate.
    """
    from decimal import Decimal, ROUND_HALF_EVEN
    from .log_capture import timestamp_ns
    if isinstance(value,str):
        try:seconds=Decimal(timestamp_ns(value))/Decimal(1_000_000_000)
        except ValueError:seconds=Decimal(str(stamp(value)))
    else:seconds=Decimal(str(value))
    milliseconds=int((seconds*1000).to_integral_value(rounding=ROUND_HALF_EVEN))
    return dict(timestamp_ms=milliseconds,
                normalization_delta_seconds=float(Decimal(milliseconds)/1000-seconds))


def agent_evidence(path):
    """Read each Agent boundary independently; never infer a preferred collector.

    The durable pre-instrumentation Node inventory defines multi-node membership.
    Components, archives and independent sampler rows must preserve that identity.
    Legacy evidence continues to use its original single-Agent directory.
    """
    from .scoring import read, rows
    from .sample_records import iter_records
    path=Path(path);root=path/'structured/agent'
    status=read(path/'structured/status.json',{})
    manifest=read(path/'manifest.json',{})
    node_inventory=read(path/'structured/nodes-before.json',{}).get('items',[])
    components=status.get('components',{})
    multi=bool(node_inventory or any(key.startswith('agent:') for key in components)
               or manifest.get('expected_nodes',1)>1 or any(p.is_dir() and (p/'pod.json').exists() for p in root.glob('*')))
    result=dict(valid=True,multinode=multi,expected_nodes=[],agents=[],errors=[])
    if not multi:
        pod=read(root/'pod.json',{})
        result['agents']=[dict(node=pod.get('spec',{}).get('nodeName'),collector_uid=pod.get('metadata',{}).get('uid'),root=str(root),capture_valid=capture_component_valid(path,'agent'))]
        if not result['agents'][0]['capture_valid']:
            result['valid']=False;result['errors'].append('Agent capture incomplete: legacy agent')
        return result
    errors=result['errors']
    expected=[node.get('metadata',{}).get('name') for node in node_inventory]
    node_uids={node.get('metadata',{}).get('name'):node.get('metadata',{}).get('uid') for node in node_inventory}
    if not expected or any(not name for name in expected) or len(set(expected))!=len(expected):
        errors.append('missing or duplicate expected node inventory')
    if len({value for value in node_uids.values() if value})!=len(expected):errors.append('missing or duplicate Node UID inventory')
    if manifest.get('expected_nodes',len(expected))!=len(expected):errors.append('expected node count differs from inventory')
    expected=sorted({name for name in expected if name});result['expected_nodes']=expected
    if (root/'pod.json').exists() or (root/'raw').exists():errors.append('mixed legacy and per-node Agent evidence')
    actual={p.name for p in root.glob('*') if p.is_dir()}
    if actual!=set(expected):errors.append('missing or unexpected Agent archive directory')
    if {key for key in components if key.startswith('agent:')}!={'agent:'+node for node in expected}:errors.append('missing or unexpected Agent capture component')
    seen_uids=set()
    for node in expected:
        directory=root/node;pod=read(directory/'pod.json',{})
        uid=pod.get('metadata',{}).get('uid');actual_node=pod.get('spec',{}).get('nodeName')
        if not uid or uid in seen_uids:errors.append('missing or duplicate Agent collector UID: '+node)
        if actual_node!=node:errors.append('Agent pod node mismatch: '+node)
        seen_uids.add(uid)
        component=components.get('agent:'+node,{})
        if component.get('collector_uid')!=uid or component.get('node_name')!=node:
            errors.append('Agent capture component identity or lifecycle invalid: '+node)
        if not node_uids.get(node):errors.append('node UID unavailable: '+node)
        if not capture_component_valid(path,'agent:'+node):
            errors.append('Agent capture incomplete: '+node)
            errors.extend('Agent capture incomplete: '+node+': '+str(error) for error in component.get('errors',[]))
            result['agents'].append(dict(node=node,node_uid=node_uids.get(node),collector_uid=uid,
                                         root=str(directory),capture_valid=False))
            # Partial frozen records are quarantined, not parser input. Preserve
            # the acquisition failure instead of masking it with a JSON exception.
            continue
        for name in ('receiver-input.ndjson','metrics.json'):
            if not (directory/'raw'/name).is_file():errors.append('missing Agent raw file: '+node+'/'+name)
        metric_count=0
        for resource,metric,kind,labels,point in raw_metrics(directory/'raw'):
            metric_count+=1
            if resource.get('k8s.node.name') and resource['k8s.node.name']!=node:
                errors.append('raw metric source node mismatch: '+node)
            if metric.get('name') in ('container.cpu.usage','container.memory.usage') and (resource.get('k8s.node.name')!=node or not resource.get('k8s.pod.uid')):
                errors.append('raw container node or Pod UID identity unavailable: '+node)
        if not metric_count:errors.append('empty Agent raw metrics: '+node)
        inputs=rows(directory/'raw/receiver-input.ndjson')
        if not inputs:errors.append('empty receiver input: '+node)
        for row in inputs:
            if row.get('collector_uid')!=uid or row.get('node_name')!=node:
                errors.append('receiver collector UID or node mismatch: '+node)
            if row.get('path','').split('?')[0].rstrip('/')=='/stats/summary' and row.get('status')==200 and row.get('body',{}).get('node',{}).get('nodeName')!=node:
                errors.append('receiver summary source node mismatch: '+node)
        independent=list(iter_records(path/('kubelet-'+node+'.ndjson')))
        if not independent:errors.append('missing independent kubelet stream: '+node)
        for row in independent:
            if row.get('node_name')!=node or row.get('node_uid')!=node_uids.get(node) or row.get('summary',{}).get('node',{}).get('nodeName')!=node or row.get('error'):
                errors.append('independent kubelet source identity invalid: '+node)
        result['agents'].append(dict(node=node,node_uid=node_uids.get(node),collector_uid=uid,root=str(directory)))
    result['errors']=sorted(set(errors));result['valid']=not errors
    return result


def resource_scores(path, nodes, cpu, memory, summaries, namespace, start, end, thresholds,
                    vm_rows=None, inventory=None):
    """Compare source values by exact storage identity, not query float times.

    API timestamp is a query evaluation instant, rounded to the declared VM
    millisecond precision alongside window bounds. OTLP timeUnixNano identifies
    the stored source point (VM truncates it to milliseconds); receiver-body
    reading.time remains the underlying kubelet measurement identity.
    """
    from .scoring import resource_scores as legacy, rows, read, state, DEFAULTS, unique
    cfg = {**DEFAULTS, **thresholds}
    agents=agent_evidence(path)
    if agents['multinode']:
        from .sample_records import iter_records
        summaries=[row for agent in agents['agents'] for row in iter_records(Path(path)/('kubelet-'+agent['node']+'.ndjson'))]
    start_identity,end_identity=vm_time_identity(start),vm_time_identity(end)
    start_ms,end_ms=start_identity['timestamp_ms'],end_identity['timestamp_ms']
    inventory_result = legacy(nodes,cpu,memory,summaries,namespace,start_ms/1000,end_ms/1000,cfg,vm_rows,inventory)
    # Refine reporting identities only; preserve the observed values and all
    # legacy lifecycle/application eligibility decisions used by scoring.
    overhead=defaultdict(set);overhead_metrics=[]
    for original in inventory_result['overhead_metrics']:
        row=dict(original);ns,pod_name,container=row['container'];category=row['category']
        if container.startswith('diag-') or pod_name.startswith('eval-capture-'):
            category='evaluation_capture'
        elif category in ('generator','eval-observer','istio-proxy','sidecar'):
            pass
        elif ns=='karthos-system':category='karthos'
        elif ns==namespace and pod_name.startswith(('otel-collector-agent-','eval-otlp-relay-')):
            category='source_collector'
        elif ns!=namespace or category=='collectors_karthos':category='cluster_other'
        row['category']=category;overhead_metrics.append(row)
        overhead[category].add(tuple(row['container']))
    inventory_result=dict(inventory_result,overhead_metrics=overhead_metrics,
        overhead={category:[list(key) for key in sorted(keys)] for category,keys in overhead.items()},
        overhead_semantics='Per-component observed usage during measurement. Incremental causal overhead requires a comparable uninstrumented baseline.')
    inputs=[row for agent in agents['agents'] if agent.get('capture_valid') is not False for row in rows(Path(agent['root'])/'raw/receiver-input.ndjson')]
    entities = {};entity_identity={}
    for node in nodes:
        if str(node.get('kind')).lower() != 'container':continue
        labels = node.get('labels', {})
        key=(node.get('namespace',labels.get('k8s.namespace.name')),labels.get('k8s.pod.name'),labels.get('k8s.container.name',node.get('name')))
        entities[node['id']] = key
        entity_identity[node['id']]={'node':labels.get('k8s.node.name'),'pod_uid':labels.get('k8s.pod.uid')}
    raw = defaultdict(list)
    for agent in agents['agents']:
        if agent.get('capture_valid') is False:continue
        uid=agent['collector_uid']
        for res,metric,kind,labels,point in raw_metrics(Path(agent['root'])/'raw'):
            name=metric['name']
            if name not in ('container.cpu.usage','container.memory.usage') or kind != 'gauge':continue
            key=(res.get('k8s.namespace.name'),res.get('k8s.pod.name'),res.get('k8s.container.name'))
            data=dict(namespace=key[0],pod=key[1],container=key[2],pod_uid=res.get('k8s.pod.uid'),
                 node=res.get('k8s.node.name'),collector_uid=uid,node_uid=agent.get('node_uid'),timestamp=int(point['timeUnixNano'])/1e9,
                 timestamp_ms=int(point['timeUnixNano'])//1000000,
                 timestamp_unix_ns=int(point['timeUnixNano']),
                 kind='cpu' if name=='container.cpu.usage' else 'memory',
                 value=float(point.get('asDouble',point.get('asInt'))),unit=metric.get('unit'),
                 source=labels.get('karthos_source'))
            if agents['multinode'] and data['node']!=agent['node']:
                agents['errors'].append('raw metric node differs from Agent archive: '+str(agent['node']));agents['valid']=False
            raw[(key,data['kind'],data['timestamp_ms'])].append(data)
    stored=defaultdict(list)
    for series in vm_rows or []:
        metric=series['metric']
        name=metric.get('__name__')
        kind={'k8s_container_cpu_usage':'cpu','k8s_container_memory_usage':'memory',
              'container_cpu_usage':'cpu','container_memory_usage':'memory'}.get(name)
        if kind:
            for t,v in zip(series['timestamps'],series['values']):
                stored[(metric.get('entity_id'),kind)].append((int(t),float(v),metric))
    expected = {(tuple(c['container']),c['metric']) for c in inventory_result['checks']}
    checks=[];seen=set()
    for kind,samples,unit in [('cpu',cpu,'millicores'),('memory',memory,'bytes')]:
        for sample in unique(samples):
            new_name='container.'+kind+'.usage'
            name=sample.get('metric_name') or 'k8s.'+new_name
            expected_unit='cores' if kind=='cpu' and name==new_name else unit
            key=entities.get(sample.get('entity_id'))
            if (key,kind) not in expected:continue
            serialized=sample.get('timestamp',sample.get('observed_at'))
            identity=vm_time_identity(serialized);evaluation_ms=identity['timestamp_ms']
            if not start_ms<=evaluation_ms<=end_ms:continue
            seen.add((key,kind))
            item=dict(container=list(key),metric=kind,evaluation_timestamp=evaluation_ms/1000,
                      evaluation_timestamp_ms=evaluation_ms,evaluation_serialized_timestamp=serialized,
                      evaluation_normalization_delta_seconds=identity['normalization_delta_seconds'],state='incomplete')
            candidates=[s for s in stored[(sample['entity_id'],kind)] if s[0]<=evaluation_ms and s[2].get('__name__')==name.replace('.','_') and partition_key(s[2])==partition_key(sample.get('labels',{}))]
            if not candidates:
                item.update(reason='missing matching stored series',preservation={'state':'incomplete'})
                checks.append(item);continue
            latest=max(s[0] for s in candidates);latest_points={(v,json.dumps(m,sort_keys=True)) for t,v,m in candidates if t==latest}
            if len(latest_points)!=1:
                item.update(reason='ambiguous stored sample identity',preservation={'state':'incomplete'})
                checks.append(item);continue
            value,metric_json=next(iter(latest_points));metric=json.loads(metric_json)
            raw_points=raw[(key,kind,latest)]
            raw_points=list({json.dumps(x,sort_keys=True):x for x in raw_points}.values())
            if len(raw_points)!=1:
                item.update(reason='missing or ambiguous raw datapoint',preservation={'state':'incomplete'})
                checks.append(item);continue
            point=raw_points[0]
            identity=entity_identity.get(sample['entity_id'],{})
            if any(identity.get(field) and identity[field]!=point.get(field) for field in ('node','pod_uid')):
                item.update(reason='graph and raw node or Pod UID identity mismatch',preservation={'state':'incomplete'})
                checks.append(item);continue
            evidence=check_resource_input(dict(point,canonical_unit=expected_unit),inputs,summaries,cfg)
            item.update(evidence,node=point['node'],pod_uid=point['pod_uid'],collector_uid=point['collector_uid'],node_uid=point.get('node_uid'),stored_timestamp=latest/1000,stored_timestamp_ms=latest,stored_value=value,
                        raw_sample_timestamp_ns=point['timestamp_unix_ns'],
                        stored_sample_age_seconds=(evaluation_ms-latest)/1000)
            canonical=evidence['canonical_value']
            preserved=canonical is not None and math.isclose(canonical,value,rel_tol=1e-12,abs_tol=1e-8) and math.isclose(value,float(sample['value']),rel_tol=1e-12,abs_tol=1e-8)
            item['preservation']=dict(state='pass' if preserved else ('incomplete' if canonical is None else 'fail'),raw=canonical,stored=value,api=sample['value'])
            item['freshness']=dict(state='pass' if 0<=item['stored_sample_age_seconds']<=cfg['max_freshness_seconds'] else 'incomplete',
                                   cache_age_seconds=item.get('cache_age_seconds'),stored_sample_age_seconds=item['stored_sample_age_seconds'])
            if metric.get('source')!='kubelet' or metric.get('karthos_unit')!=expected_unit or sample.get('unit')!=expected_unit:
                item['preservation'].update(state='fail',reason='wrong canonical source or unit')
            item['state']=state([item['conversion'],item['preservation'],item['independent_agreement'],item['freshness']])
            checks.append(item)
    for key,kind in sorted(expected-seen):
        checks.append(dict(container=list(key),metric=kind,state='incomplete',reason='missing API samples for expected active container'))
    return dict(inventory_result,checks=checks,state=state(checks) if agents['valid'] else 'incomplete',agent_evidence=agents,reference_contract='exact receiver input; independent agreement requires same underlying measurement',
                verifier_version='4.0.0',
                time_identity=dict(precision='VictoriaMetrics integer milliseconds',normalization='round query evaluation and window bounds to nearest millisecond; ties to even',
                    window_start_ms=start_ms,window_end_ms=end_ms,window_start_original=start,window_end_original=end,
                    window_start_normalization_delta_seconds=start_identity['normalization_delta_seconds'],
                    window_end_normalization_delta_seconds=end_identity['normalization_delta_seconds'],
                    query_time='API evaluation time selects latest stored point at or before the same integer millisecond',
                    source_time='Raw OTLP nanoseconds identify source datapoint; VM ingestion truncates to milliseconds; receiver reading.time is underlying kubelet measurement'))
