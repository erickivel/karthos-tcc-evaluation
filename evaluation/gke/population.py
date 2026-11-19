"""Controlled aggregate population proof, not storage of individual requests.

The oracle is the independently recorded checkout operation set. Native source
and stored partitions must be unique, reset-free, scoped to that edge/source,
and guarded against extra operations in their actual counter slack. Histogram
counts require their own membership check. API event-time fidelity is separate.
"""
from collections import Counter
import math
from ..timing_analysis import fixed_window_error_reference

MESH={'source_canonical_service':'edge_client','destination_canonical_service':'edge_server',
      'source_workload_namespace':'client_namespace','destination_service_namespace':'server_namespace',
      'request_protocol':'network.protocol.name','response_code':'http.response.status_code',
      'grpc_response_status':'rpc.grpc.status_code','response_flags':'mesh.envoy.response_flags',
      'connection_security_policy':'mesh.security.policy','source_principal':'mesh.peer.source.principal',
      'destination_principal':'mesh.peer.destination.principal'}
EDGE=('edge_client','edge_server','client_namespace','server_namespace')
META={'__name__','karthos_unit','entity_id','edge_client_id','edge_server_id','http_status_code'}


def resolve_scope(traffic,spec):
    """Recover only an explicit frozen demo, never infer it from a path substring."""
    result=dict(traffic);manifest=dict(traffic.get('manifest',{}));demo=spec.get('demo')
    if demo is not None:
        if demo not in ('boutique','astronomy'):raise ValueError('unsupported frozen demo')
        if manifest.get('demo') not in (None,demo):raise ValueError('conflicting frozen demo scope')
        manifest['demo']=demo
    result['manifest']=manifest
    bounds=traffic.get('windows',{}).get('whole',{}).get('bounds_seconds',[])
    if len(bounds)==2:
        result['fixed_window_error_reference']=fixed_window_error_reference(manifest,result,traffic.get('events',{}).get('observers',[]),traffic.get('events',{}).get('traces',[]),*bounds)
    return result


def _key(labels,source,native=False):
    if native:labels={target:labels[key] for key,target in MESH.items() if key in labels}
    if source=='mesh.istio':keys=set(MESH.values())
    else:keys=set(EDGE)|{'failed'}
    if not native and set(labels)-keys-META-{'source'}:raise ValueError('unmapped stored semantic dimensions')
    if not native and labels.get('http_status_code') not in (None,labels.get('http.response.status_code')):raise ValueError('conflicting HTTP status aliases')
    if not all(labels.get(k) for k in EDGE):raise ValueError('incomplete edge identity')
    return tuple(sorted((k,str(v)) for k,v in labels.items() if k in keys))


def prove(name,traffic,verification):
    result={'state':'insufficient','version':'controlled-population-v2','individual_stored_request_ids_supported':False,
            'scope':'independent selected-edge/source partition accounting; API event-time population separate'}
    def blocked(reason,**extra):return dict(result,reason=reason,**extra)
    try:
        manifest=traffic.get('manifest',{});source=manifest.get('source_id');namespace=manifest.get('namespace')
        whole=traffic.get('windows',{}).get('whole',{});start,end=whole['bounds_seconds']
        if source not in ('mesh.istio','traces.servicegraph'):return blocked('unsupported or missing selected source')
        if traffic.get('independent_valid') is not True or traffic.get('required_evidence',{}).get('valid') is not True:return blocked('required independent recorder evidence unavailable')
        observers=traffic.get('events',{}).get('observers',[]);traces=traffic.get('events',{}).get('traces',[])
        reference=fixed_window_error_reference(manifest,traffic,observers,traces,start,end)
        result['independent_reference']=reference
        if reference.get('state')!='available':return blocked(reference.get('reason','independent reference unavailable'))
        target=reference['target'];native=[r for r in verification.get('source',{}).get('series',[]) if r.get('metric')=='istio_requests_total']
        if source=='mesh.istio':
            expected={}
            for row in native:
                labels=row.get('labels',{})
                if labels.get('reporter')!='destination' or labels.get('destination_service_name')!=target:raise ValueError('native source edge/reporter scope mismatch')
                key=_key(labels,source,True)
                if key in expected:raise ValueError('ambiguous native source partitions')
                if row.get('resets')!=0 or row.get('increase') is None:raise ValueError('native source counter reset or missing bracket')
                expected[key]=row['increase']
            if not expected:return blocked('native selected source partitions unavailable')
        else:
            expected=Counter()
            for event in traces:
                if event.get('paired_frontend') and event.get('target_service')==target and start<=event['start']<=event['completion']<end:
                    labels={'edge_client':'frontend','edge_server':target,'client_namespace':namespace,'server_namespace':namespace,'failed':str(event['connector_failed']).lower()}
                    expected[_key(labels,source)]+=1
            if sum(expected.values())!=reference['requests']:return blocked('paired trace population differs from the independent reference')
        # Full actual labels are retained below; projection is only the pinned
        # source contract. Unknown stored labels and projection collisions block.
        stored=[r for r in verification.get('vm',{}).get('series',[]) if isinstance(r.get('metric'),dict) and r['metric'].get('__name__')=='servicegraph_request_count']
        def check_rows(rows,native_rows=False):
            found={}
            for row in rows:
                labels=row.get('labels',{}) if native_rows else row['metric']
                if not native_rows and labels.get('source')!=source:raise ValueError('wrong stored source')
                key=_key(labels,source,native_rows);edge=dict(key)
                if (edge['edge_client'],edge['edge_server'],edge['client_namespace'],edge['server_namespace'])!=('frontend',target,namespace,namespace):raise ValueError('partition edge/namespace scope mismatch')
                if key in found:raise ValueError('ambiguous projected partitions')
                if row.get('resets')!=0 or not all(type(row.get(k)) in (int,float) and math.isfinite(row[k]) for k in ('first','last','increase')) or row['first']>row['last']:raise ValueError('counter reset, nonfinite value or missing bracket')
                found[key]=row
            if set(found)-set(expected) or any(k not in found and v!=0 for k,v in expected.items()):raise ValueError('missing or unexpected outcome partitions')
            discrepancies=[{'identity':dict(k),'expected':expected.get(k,0),'observed':r['increase'],
                            'observation_bounds':[r['first'],r['last']],
                            'metric':r['metric'] if native_rows else r['metric']['__name__'],
                            'boundary':'source' if native_rows else 'storage',
                            'cause_confidence':'unknown'}
                           for k,r in found.items() if r['increase']!=expected.get(k,0)]
            if discrepancies:
                result['partition_discrepancies']=discrepancies
                raise ValueError('partition totals do not establish the expected controlled population')
            return found
        found=check_rows(stored)
        if not found:return blocked('stored request counter partitions unavailable')
        guarded=list(stored)+(native if source=='mesh.istio' else [])
        if name=='servicegraph.request.duration':
            hist=[r for r in verification.get('vm',{}).get('series',[]) if isinstance(r.get('metric'),dict) and r['metric'].get('__name__')=='servicegraph_request_duration_count']
            check_rows(hist)
            if not hist:return blocked('stored histogram population unavailable')
            guarded+=hist
            if source=='mesh.istio':
                native_hist=[r for r in verification.get('source',{}).get('series',[]) if r.get('metric')=='istio_request_duration_milliseconds_count']
                check_rows(native_hist,True)
                if not native_hist:return blocked('native histogram population unavailable')
                guarded+=native_hist
        lo=min(r['first'] for r in guarded);hi=max(r['last'] for r in guarded)
        from ..scoring import stamp
        capture=traffic.get('structured_capture',{})
        if capture.get('valid') is not True or traffic.get('capture_integrity',{}).get('valid') is not True or not stamp(capture['started_at'])<=lo<=hi<=stamp(capture['finished_at']):return blocked('capture lifetime does not enclose actual counter guard')
        checkout=[r for r in observers if r.get('boundary')=='checkout' and (r.get('uri') or '').endswith('/PlaceOrder')]
        outside=[r['identity'] for r in checkout if r['completion']>=lo and r['start']<=hi and not start<=r['start']<=r['completion']<end]
        if outside:return blocked('extra or crossing checkout operations in actual counter boundary slack',outside_operation_ids=outside,counter_guard=[lo,hi])
        uncertainty=traffic.get('clock',{}).get('uncertainty_seconds')
        if type(uncertainty) not in (int,float) or not 0<=uncertainty<.25:return blocked('clock evidence unavailable for boundary membership')
        if any(min(abs(r[t]-b) for t in ('start','completion') for b in (start,end))<=uncertainty for r in checkout if r['completion']>=lo and r['start']<=hi):return blocked('operation membership uncertain within recorded clock bound')
        result.update(state='pass',expected_requests=reference['requests'],expected_errors=reference['errors'],counter_guard=[lo,hi],
            source_partition_identities=[r.get('labels') for r in native],stored_partition_identities=[r['metric'] for r in stored],
            expected_partitions=[{'identity':dict(k),'requests':v} for k,v in expected.items()],
            limitation='controlled aggregate evidence cannot exclude compensating loss and duplication; not per-request storage or exact API event-time fidelity')
        return result
    except (KeyError,TypeError,ValueError) as exc:return blocked('population evidence unavailable: '+str(exc))


def settled_duration(traffic,native_series,stored_series,quiet_bounds,thresholds):
    """Separate diagnostic at the already-recorded quiet bounds; never move load."""
    result={'state':'insufficient','scope':'recorded quiet-boundary source/store duration; original fixed API window unchanged',
            'fixed_workload_bounds':traffic.get('windows',{}).get('whole',{}).get('bounds_seconds'),'recorded_quiet_bounds':quiet_bounds}
    try:
        source=traffic.get('manifest',{}).get('source_id')
        if source!='mesh.istio':return dict(result,state='not-applicable',reason='native trace populations are checked directly')
        whole=dict(traffic['windows']['whole'],raw_mesh_source={'series':native_series})
        view=dict(traffic,windows=dict(traffic['windows'],whole=whole))
        proof=prove('servicegraph.request.duration',view,{'source':{'series':native_series},'vm':{'series':stored_series}})
        result['population_proof']=proof
        if proof['state']!='pass':return dict(result,reason=proof.get('reason'))
        def mean(rows,native):
            sums={};counts={}
            for row in rows:
                metric=row['metric'] if native else row['metric']['__name__']
                suffix=metric.rsplit('_',1)[-1]
                if ('duration_milliseconds_' if native else 'request_duration_') not in metric or suffix not in ('sum','count'):continue
                labels=row['labels'] if native else row['metric'];key=_key(labels,source,native)
                target=sums if suffix=='sum' else counts
                if key in target:raise ValueError('ambiguous settled histogram partition')
                if row.get('resets')!=0 or row.get('increase') is None:raise ValueError('histogram reset or missing bracket')
                if not native and suffix=='sum' and labels.get('karthos_unit')!='seconds':raise ValueError('stored histogram sum unit is not seconds')
                target[key]=row
            if not sums or set(sums)!=set(counts):raise ValueError('missing matching histogram sums/counts')
            if any((r['first'],r['last'])!=(counts[k]['first'],counts[k]['last']) for k,r in sums.items()):raise ValueError('histogram sum/count observation brackets differ')
            count=sum(r['increase'] for r in counts.values());total=sum(r['increase'] for r in sums.values())*(1 if native else 1000)
            if count<=0:raise ValueError('empty duration population')
            return total/count,count
        native_mean,native_count=mean(native_series,True);stored_mean,stored_count=mean(stored_series,False)
        from ..scoring import compare,DEFAULTS
        result.update(compare(native_mean,stored_mean,thresholds.get('duration_relative_tolerance',DEFAULTS['duration_relative_tolerance']),thresholds.get('duration_absolute_tolerance_ms',DEFAULTS['duration_absolute_tolerance_ms'])))
        result.update(source_mean_ms=native_mean,stored_mean_ms=stored_mean,source_count=native_count,stored_count=stored_count,
                      limitation='separate settled diagnostic; no substitution for fixed-window API membership')
        return result
    except (KeyError,ValueError,TypeError) as exc:return dict(result,reason=str(exc))


def settled_diagnostics(traffic,native_series,quiet_stored_series,frozen_stored_series,bounds,thresholds):
    """Always report both scopes; no fallback selection based on passing values."""
    quiet=settled_duration(traffic,native_series,quiet_stored_series,bounds,thresholds)
    frozen=settled_duration(traffic,native_series,frozen_stored_series,bounds,thresholds)
    frozen['scope']='native histogram at recorded quiet bounds versus original frozen stored population; not quiet-end storage coverage'
    return {'quiet_to_quiet':quiet,'native_settled_to_frozen_store':frozen}
