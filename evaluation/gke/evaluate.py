"""Per-window qualification evidence. Offline construction performs no I/O to a cluster.

Comparison claims are deliberately narrower than retained observations. Host gauges
need the same source timestamp; acquisition overlap alone does not establish gauge
truth. Monotonic source counters can be bounded by disjoint independent reads.
API rate query fidelity remains the product compiler/parity verifier's concern.

Pinned semantics reviewed for this adapter:
https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.156.0/receiver/hostmetricsreceiver/internal/scraper/memoryscraper/memory_scraper_linux.go
https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.156.0/receiver/hostmetricsreceiver/internal/scraper/cpuscraper/cpu_scraper.go
The memory scraper links this pinned implementation (MemFree/Buffers/slab *1024):
https://github.com/shirou/gopsutil/blob/d8750909ba41f2de9750c90a6d2074c68dfc677e/mem/mem_linux.go
Product pkg/sources/hostos/{parser,normalization}.go: utilization ratio *100,
other native values unchanged; network device becomes network_interface_name.
Linux MemFree/Buffers/slab fields use kB*1024. The v0.156 memory used-state
formula depends on the MemAvailable feature gate; we never guess that gate.
Disk sectors use the pinned 512-byte conversion. CPU intervals, filesystem
device identities and underlying read timestamps must still be explicitly attested.
"""
from __future__ import annotations

import json
import math
import re
import time
import urllib.parse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .models import read as load, write
from .. import runner as r
from .. import evidence_v4, timing_analysis
from ..sample_records import iter_records
from ..series_identity import partition_key

UPSTREAM_VERSION='0.156.0'
MAX_EXPORT_BYTES=512*1024*1024
MEMORY_FIELDS={'free':'MemFree','buffered':'Buffers','slab_reclaimable':'SReclaimable','slab_unreclaimable':'SUnreclaim'}


def _read(path,default=None):
    return load(path) if Path(path).exists() else ({} if default is None else default)


def _time(value):
    if isinstance(value,(int,float)) and not isinstance(value,bool):return float(value)
    return datetime.fromisoformat(str(value).replace('Z','+00:00')).timestamp()


def _iso(value):
    return datetime.fromtimestamp(_time(value),timezone.utc).isoformat()


def _definitions(ctx,name):
    from .models import definition_path
    return load(definition_path(ctx,name))


def _samples(document):
    return document.get('data',{}).get('samples',[]) if isinstance(document,dict) else []


def _instrument_contract(metric,kind):
    body=metric.get(kind,{})
    return {'kind':kind,'is_monotonic':body.get('isMonotonic',False) if kind=='sum' else None,
            'aggregation_temporality':body.get('aggregationTemporality')}


def _numerical_scope(checks,selected,resources,cpu_name,memory_name):
    # Traffic has its own population-aware verifier; catalog presence is not accuracy.
    names={row['name'] for row in selected if not row['name'].startswith('servicegraph.')}
    names-={cpu_name,memory_name}
    scoped=[check for check in checks if check.get('observed',{}).get('metric',check.get('metric')) in names]
    represented={check.get('observed',{}).get('metric',check.get('metric')) for check in scoped}
    for name in sorted(names-represented):
        scoped.append({'metric':name,'state':'insufficient','reason':'selected independent reference or exact measurement identity unavailable'})
    scoped.append({'metric':cpu_name+' + '+memory_name,'state':{'pass':'pass','fail':'fail'}.get(resources.get('state'),'insufficient'),'report':resources})
    counts={key:sum(check.get('state')==key for check in scoped) for key in ('pass','fail','insufficient','not-applicable')}
    counts['eligible']=len(scoped)-counts['not-applicable']
    state='fail' if counts['fail'] else ('insufficient' if counts['insufficient'] or not counts['eligible'] else 'pass')
    return {'state':state,'checks':scoped,'counts':counts,'scope':'declared numerical resource comparisons; traffic and catalog visibility reported separately'}


def _safe_metric(name):
    if not re.fullmatch(r'[a-z][a-z0-9_.]*',name):raise ValueError('unsafe metric identifier')
    return name


def capture_extended(ctx,cell,path,start,end):
    """Explicit-target exports; caller holds the reviewed runner target adapter.

    API pages, unmodified envelopes, source assignments and raw VM labels/units
    are retained. No aggregate, node shortcut, first VictoriaMetrics service,
    or source arbitration choice is invented here.
    """
    path=Path(path);root=path/'extended';root.mkdir(parents=True,exist_ok=False)
    definition=_definitions(ctx,'coverage.yaml');selected=_definitions(ctx,'metrics.yaml')
    names=[_safe_metric(row['name']) for row in definition['entries']]
    if len(names)!=43 or len(set(names))!=43:raise ValueError('frozen catalog must contain exactly 43 unique metric names')
    status={'state':'incomplete','errors':[],'start':start,'end':end,'metrics':names,
            'accuracy_selected':[row['name'] for row in selected['metrics']],
            'requests':[],'explicit_target':'caller-owned runner adapter and ctx kubeconfig'}
    def request(label,operation):
        started=time.time_ns()
        try:return operation()
        except Exception as exc:
            status['errors'].append(label+': '+str(exc));return None
        finally:status['requests'].append({'operation':label,'requested_at_ns':started,'received_at_ns':time.time_ns()})
    deployed_plan={}
    contract=request('source-plan',lambda:ctx.json('get','configmap','karthos-source-contract','-n','karthos-system'))
    if contract is not None:
        ctx.write(root/'source-contract.json',contract)
        try:
            deployed_plan=json.loads(contract['data']['source-plan.json']);ctx.write(root/'source-plan.json',deployed_plan)
        except (KeyError,TypeError,ValueError) as exc:status['errors'].append('source-plan decode: '+str(exc))
    node_inventory=request('Kubernetes Node identity',lambda:ctx.json('get','nodes'))
    node_uids={row['metadata']['name']:row['metadata'].get('uid') for row in (node_inventory or {}).get('items',[])}
    snapshots=[]
    for namespace in (cell.namespace,'karthos-system'):
        started=time.time()
        inventory=request('Kubernetes inventory:'+namespace,lambda ns=namespace:ctx.json('get','pods','-n',ns))
        received=time.time()
        if inventory is not None:snapshots.append({'namespace':namespace,'acquisition_started_at':started,'acquisition_ended_at':received,'items':inventory.get('items',[]),'node_uids':node_uids,'measurement_timestamp':None,'semantics':'API acquisition bracket; informer/source object version not inferred'})
    ctx.write(root/'kubernetes-inventory.json',{'snapshots':snapshots})
    try:
        with r.forward('deployment/karthos-backend','karthos-system',28983,8080) as base:
            catalog=request('catalog',lambda:r.api_pages(base,'/api/v1/metrics/catalog',{},'metrics',root/'pages/catalog'))
            if catalog is not None:ctx.write(root/'catalog.json',catalog)
            stats=request('catalog-stats',lambda:r.get_json(base+'/api/v1/metrics/catalog/stats'))
            if stats is not None:ctx.write(root/'catalog-stats.json',stats)
            for name in names:
                params={'metric_name':name,'from':_iso(start),'to':_iso(end),'step':'15s'}
                assigned=deployed_plan.get('metric_assignments',{}).get(name)
                if assigned:params['source']=assigned
                response=request('metric:'+name,lambda n=name,p=params:r.api_pages(base,'/api/v1/metrics',p,'samples',root/'pages'/n))
                if response is not None:ctx.write(root/'api'/(name+'.json'),response)
    except Exception as exc:status['errors'].append('API forward: '+str(exc))
    services=request('VM inventory',lambda:ctx.json('get','services','-n','karthos-system'))
    candidates=[item['metadata']['name'] for item in (services or {}).get('items',[]) if 'victoria' in item['metadata']['name']]
    if len(candidates)!=1:status['errors'].append('VictoriaMetrics service unavailable or ambiguous')
    else:
        expression='|'.join(re.escape(name.replace('.','_'))+'(_sum|_count|_bucket)?' for name in names)
        selector='{__name__=~"'+expression+'"}'
        params={'match[]':selector,'start':_iso(_time(start)-60),'end':_iso(_time(end)+90)}
        ctx.write(root/'vm-export-request.json',params)
        try:
            with r.forward('service/'+candidates[0],'karthos-system',28929,8428) as base:
                def export():
                    with r.urllib.request.urlopen(base+'/api/v1/export?'+urllib.parse.urlencode(params),timeout=r.http_timeout(30)) as response:
                        raw=response.read(MAX_EXPORT_BYTES+1)
                    if len(raw)>MAX_EXPORT_BYTES:raise ValueError('extended VM export exceeds byte bound')
                    text=raw.decode('utf-8')
                    for line in text.splitlines():
                        if line.strip():json.loads(line)
                    return text
                raw=request('VM raw export',export)
                if raw is not None:ctx.write(root/'vm-raw.ndjson',raw)
                from . import parity
                parity_report=request('selected API/storage parity',lambda:parity.capture(ctx,root,base,status['accuracy_selected'],deployed_plan.get('metric_assignments',{}),start,end))
                if parity_report is not None:ctx.write(root/'parity/manifest.json',parity_report)
        except Exception as exc:status['errors'].append('VM forward: '+str(exc))
    status['state']='complete' if not status['errors'] else 'incomplete'
    ctx.write(root/'capture-status.json',status)
    return status


def catalog_contract(definition, catalog):
    """Compare the full public contract frozen from the pinned product revision."""
    expected={row['name']:row.get('contract') for row in definition['entries']}
    items=catalog.get('data',{}).get('metrics',[])
    observed={row['name']:row for row in items}
    missing=sorted(set(expected)-set(observed))
    unexpected=sorted(set(observed)-set(expected))
    mismatches={name:[key for key,value in spec.items() if observed[name].get(key)!=value]
                for name,spec in expected.items() if spec and name in observed}
    mismatches={name:fields for name,fields in mismatches.items() if fields}
    valid=(len(expected)==definition['expected_count']==len(items)==len(observed)
           and not (missing or unexpected or mismatches))
    return dict(valid=valid,missing=missing,unexpected=unexpected,mismatches=mismatches)


def _classified_mesh_outcome(labels):
    grpc=labels.get('rpc.grpc.status_code','')
    if grpc:
        return re.fullmatch(r'0|[1-9][0-9]*',str(grpc)) is not None
    return labels.get('network.protocol.name')!='grpc' and re.fullmatch(
        r'[1-5][0-9][0-9]',str(labels.get('http.response.status_code',''))) is not None


def catalog_coverage(definition,catalog,source_plan,api,stored,start,end,thresholds):
    """A fixed row per catalog item, with explicit source/dimensional evidence."""
    metadata={row['name']:row for row in catalog.get('data',{}).get('metrics',[])}
    assignments=source_plan.get('metric_assignments',{})
    entries=[];freshness=thresholds.get('max_freshness_seconds',60)
    for spec in definition['entries']:
        name=spec['name'];samples=_samples(api.get(name,{}));source=assignments.get(name)
        query_view=(definition.get('contract') in ('thesis-qualification-v2','thesis-qualification-v3')
                    and name=='servicegraph.request.error.count')
        mesh_view=query_view and source=='mesh.istio'
        input_name='servicegraph.request.count' if mesh_view else name
        vm_name=input_name.replace('.','_')
        rows=[row for row in stored if row.get('metric',{}).get('__name__') in
              (vm_name,vm_name+'_sum',vm_name+'_count',vm_name+'_bucket')
              and (not mesh_view or _classified_mesh_outcome(row['metric']))]
        reasons=[];identities=[]
        if name not in metadata:reasons.append('catalog metadata unavailable')
        if not source:reasons.append('metric has no deployed source assignment')
        if not samples:reasons.append('API samples unavailable')
        if not rows:reasons.append('stored series unavailable')
        for sample in samples:
            labels=sample.get('labels',{});entity=sample.get('entity_id');edge=sample.get('edge_id')
            observed_source=labels.get('source')
            if query_view:
                derivation=sample.get('derivation') or {}
                derivation_id='istio.request-errors' if mesh_view else 'servicegraph.connector-failures'
                if (derivation.get('id'),str(derivation.get('version')),derivation.get('input_metric'))!=(derivation_id,'1',input_name):
                    reasons.append('wrong or missing public derivation contract')
                if mesh_view and not _classified_mesh_outcome(labels):
                    reasons.append('unclassified outcome cannot establish a mesh error sample')
            identity={'entity_id':entity,'edge_id':edge,'labels':labels,'unit':sample.get('unit'),'evaluation_timestamp':sample.get('timestamp',sample.get('observed_at'))}
            identities.append(identity)
            if not observed_source or observed_source!=source:reasons.append('API sample source differs from deployed assignment')
            try:
                at=_time(sample.get('timestamp',sample.get('observed_at')))
                if not _time(start)<=at<=_time(end) or _time(end)-at>freshness:reasons.append('API sample stale or outside fixed window')
            except (TypeError,ValueError):reasons.append('API sample timestamp unavailable')
            matches=[row for row in rows if row['metric'].get('entity_id')==entity and row['metric'].get('edge_id')==edge and partition_key(row['metric'])==partition_key(labels)]
            identity['stored_identity_matched']=bool(matches)
            if not matches:reasons.append('complete source/dimensions not preserved in stored series')
            elif not any(any(_time(start)-freshness<=float(t)/1000<=_time(end) for t in row.get('timestamps',[])) for row in matches):reasons.append('matching stored series has no fresh points')
        classification='observed' if not reasons else ('unsupported-source' if name in metadata and not source else 'missing-expected')
        entries.append(dict(name=name,accuracy_selected=spec.get('accuracy_selected',False),classification=classification,
                            metadata=metadata.get(name),assigned_source=source,api_sample_count=len(samples),stored_series_count=len(rows),
                            identities=identities,reasons=sorted(set(reasons)),numerical_accuracy_established=False))
    expected={row['name'] for row in definition['entries']}
    contract=catalog_contract(definition,catalog) if definition.get('contract') else None
    return dict(expected_count=definition['expected_count'],entries=entries,counts=dict(Counter(row['classification'] for row in entries)),
                contract=contract,complete=(len(entries)==definition['expected_count']==len(expected)
                          and (contract is None or contract['valid'])),
                catalog_unexpected=sorted(set(metadata)-expected),catalog_missing=sorted(expected-set(metadata)),
                observed_means='metadata, deployed assignment, fresh API and stored series, complete source/dimension identity; not numerical accuracy')


# Contracts below are fixed to collector v0.156.0 and hostmetrics' gopsutil v4.26.5.
# https://github.com/shirou/gopsutil/blob/v4.26.5/disk/disk_linux.go (sectorSize=512)
# https://github.com/shirou/gopsutil/blob/v4.26.5/disk/disk_unix.go (Free=Bavail)
# https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.156.0/receiver/hostmetricsreceiver/internal/scraper/diskscraper/disk_scraper_others.go
# https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.156.0/receiver/hostmetricsreceiver/internal/scraper/filesystemscraper/filesystem_scraper_unix.go
# https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.156.0/receiver/hostmetricsreceiver/internal/scraper/cpuscraper/ucal/cpu_utilization_calculator.go
# https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.156.0/receiver/k8sclusterreceiver/internal/{pod/pods.go,container/containers.go}


def _comparison(observed,references):
    return {'state':'insufficient','observed':observed,'reference_records_available':len(references),
            'scope':'raw source/reference comparison; API preservation and population checks remain separate'}


def _blocked(result,reason,acquisition):
    result.update(reason=reason,required_acquisition=acquisition)
    return result


def _same_time(a,b):
    try:return evidence_v4.measurement_time_ns(a)==evidence_v4.measurement_time_ns(b)
    except (ValueError,TypeError):return False


def _valid_bracket(row):
    try:
        a,b=float(row['acquisition_started_at']),float(row['acquisition_ended_at'])
        return math.isfinite(a) and math.isfinite(b) and a<=b
    except (KeyError,TypeError,ValueError):return False


def _numeric(result,value,reference,**extra):
    if not all(math.isfinite(float(v)) for v in (value,reference)):
        return _blocked(result,'nonfinite comparison value','finite source and reference values')
    result.update(state='pass' if math.isclose(float(value),float(reference),rel_tol=1e-12,abs_tol=1e-9) else 'fail',reference_value=reference,**extra)
    return result


def _host_lifecycle(result,rows):
    if not rows:return _blocked(result,'missing exact node/source/dimension/unit reference','independent records for the complete selected series')
    if any(not row.get('observer_pod_uid') or not row.get('boot_id') or not _valid_bracket(row) for row in rows):
        return _blocked(result,'reference boot/observer lifecycle or acquisition bracket unavailable','boot ID, observer Pod UID and actual acquisition brackets')
    if len({(row['boot_id'],row['observer_pod_uid']) for row in rows})!=1:
        return _blocked(result,'reference lifecycle changed','one unambiguous boot/observer epoch')
    return None


def _source_bounds(observed):
    if observed.get('measurement_timestamp') is not None:
        at=_time(observed['measurement_timestamp']);return (at,at)
    pair=observed.get('source_read_bracket')
    if not isinstance(pair,(list,tuple)) or len(pair)!=2:return None
    a,b=map(float,pair)
    return (a,b) if math.isfinite(a) and math.isfinite(b) and a<=b else None


def _cumulative(observed):
    return observed.get('aggregation_temporality') in (2,'AGGREGATION_TEMPORALITY_CUMULATIVE','cumulative')


def _counter_reference(result,observed,rows,factor):
    invalid=_host_lifecycle(result,rows)
    if invalid:return invalid
    if observed.get('kind')!='sum' or observed.get('is_monotonic') is not True or not _cumulative(observed):
        return _blocked(result,'explicit monotonic source counter contract required','OTLP cumulative Sum isMonotonic=true and unaggregated source unit')
    try:bounds=_source_bounds(observed)
    except (TypeError,ValueError):bounds=None
    if bounds is None:return _blocked(result,'underlying source read time/bracket unavailable; recorded OTLP timestamp is not substituted','collector source-read acquisition bracket or exact source measurement timestamp')
    if any(row.get('measurement_timestamp') is not None for row in rows):return _blocked(result,'source-time and acquisition-time counter populations cannot be mixed','a consistent independent acquisition-bracket counter stream')
    ordered=sorted(rows,key=lambda row:(row['acquisition_started_at'],row['acquisition_ended_at']))
    if any(b['acquisition_started_at']<=a['acquisition_ended_at'] for a,b in zip(ordered,ordered[1:])):
        return _blocked(result,'duplicate or overlapping independent acquisition brackets','unique disjoint independent reads')
    if any(float(b['value'])<float(a['value']) for a,b in zip(ordered,ordered[1:])):return _blocked(result,'reference counter reset','one monotonic counter epoch')
    pairs=[(a,b) for a,b in zip(ordered,ordered[1:]) if a['acquisition_ended_at']<=bounds[0] and bounds[1]<=b['acquisition_started_at']]
    if len(pairs)!=1:return _blocked(result,'missing or ambiguous enclosing monotonic acquisition pair','two disjoint independent reads enclosing the entire source read bracket')
    a,b=pairs[0];values=[float(a['value'])*factor,float(b['value'])*factor];value=float(observed['value'])
    result.update(state='pass' if math.isfinite(value) and values[0]<=value<=values[1] else 'fail',reference_bounds=values,conversion_factor=factor,
                  source_read_bracket=list(bounds),matching_references=[a,b],reference_acquisition_brackets=[[a['acquisition_started_at'],a['acquisition_ended_at']],[b['acquisition_started_at'],b['acquisition_ended_at']]],
                  comparison='monotonic source value inside independent acquisition bounds; no API rate fidelity claim')
    return result


def _filesystem_reference(observed,references,result):
    dims=observed.get('dimensions',{});allowed={'device','mountpoint','state','type','mode'}
    if set(dims)-allowed or not dims.get('device') or not dims.get('mountpoint'):
        return _blocked(result,'complete filesystem mount/device dimensions unavailable','exact host mountpoint, device, state and any filesystem type/mode dimensions')
    name=observed['metric'];state=dims.get('state')
    if observed.get('kind')=='sum' and (observed.get('is_monotonic') is not False or not _cumulative(observed)):
        return _blocked(result,'filesystem usage is a non-monotonic cumulative level, not a delta/rate','v0.156 non-monotonic cumulative Sum contract')
    fields={'used':['used'],'free':['available'],'reserved':['free','available']}.get(state) if name=='system.filesystem.usage' else ['used','available'] if name=='system.filesystem.utilization' and state is None else None
    if not fields:return _blocked(result,'unsupported filesystem state','v0.156 used/free/reserved usage or unpartitioned utilization')
    utilization=name.endswith('.utilization')
    factor=100 if observed.get('unit')=='percent' else 1
    if observed.get('unit') not in (('ratio','percent') if utilization else ('bytes','By','B')):
        return _blocked(result,'filesystem source unit mismatch','native byte usage or canonical utilization')
    ref_dims={'device':dims['device'],'mount':dims['mountpoint']}
    if 'type' in dims:ref_dims['filesystem_type']=dims['type']
    if 'mode' in dims:ref_dims['mode']=dims['mode']
    rows=[row for row in references if row.get('source')=='statvfs' and row.get('node')==observed['node'] and row.get('node_uid')==observed['node_uid'] and row.get('unit')=='bytes' and row.get('dimensions')==ref_dims]
    if observed.get('measurement_timestamp') is None:return _blocked(result,'filesystem underlying measurement alignment unavailable','same-source-timestamp statvfs records plus attested exact host mount/device/type/mode mapping')
    selected={field:[row for row in rows if row.get('metric')=='filesystem.'+field and _same_time(row.get('measurement_timestamp'),observed['measurement_timestamp'])] for field in fields}
    if any(len(values)!=1 for values in selected.values()):return _blocked(result,'missing or ambiguous filesystem state measurement','one aligned statvfs row per required state and exact mount/device identity')
    matched=[selected[field][0] for field in fields]
    if any(not row.get('observer_pod_uid') or not _valid_bracket(row) for row in matched) or len({(row['observer_pod_uid'],row['acquisition_started_at'],row['acquisition_ended_at']) for row in matched})!=1:
        return _blocked(result,'filesystem state rows do not share one statvfs read/lifecycle','all component fields from one statvfs syscall bracket and observer Pod UID')
    values={field:float(selected[field][0]['value']) for field in fields}
    if name.endswith('.utilization'):
        denominator=values['used']+values['available']
        if denominator<=0:return _blocked(result,'filesystem utilization denominator non-positive','positive used + available capacity')
        reference=factor*values['used']/denominator
    else:reference=values['free']-values['available'] if state=='reserved' else values[fields[0]]
    return _numeric(result,observed['value'],reference,matching_references=matched,conversion_factor=factor if utilization else 1,
                    comparison='v0.156 filesystem free=Bavail; reserved=Bfree-Bavail; utilization=used/(used+available)')


def _cpu_reference(observed,references,result):
    dims=observed.get('dimensions',{});states=('user','nice','system','idle','iowait','irq','softirq','steal')
    target={'wait':'iowait','interrupt':'irq'}.get(dims.get('state'),dims.get('state'))
    interval=observed.get('measurement_interval')
    if set(dims)!={'cpu','state'} or target not in states or not isinstance(interval,(list,tuple)) or len(interval)!=2 or observed.get('unit') not in ('ratio','percent'):
        return _blocked(result,'CPU utilization requires complete per-CPU/state and aligned source interval','exact two underlying tick measurement timestamps, CPU ID/state and percent unit')
    rows=[row for row in references if row.get('source')=='linux-proc' and row.get('metric')=='cpu.time' and row.get('unit')=='ticks' and row.get('node')==observed['node'] and row.get('node_uid')==observed['node_uid'] and row.get('dimensions',{}).get('cpu')==dims['cpu'] and set(row.get('dimensions',{}))=={'cpu','state'}]
    invalid=_host_lifecycle(result,rows)
    if invalid:return invalid
    frames=[]
    for timestamp in interval:
        frame={state:[row for row in rows if row['dimensions']['state']==state and _same_time(row.get('measurement_timestamp'),timestamp)] for state in states}
        if any(len(values)!=1 for values in frame.values()):return _blocked(result,'missing or ambiguous exact CPU tick frame','all eight non-guest CPU states at both exact source interval endpoints')
        frames.append({state:float(values[0]['value']) for state,values in frame.items()})
    delta={state:frames[1][state]-frames[0][state] for state in states};total=sum(delta.values())
    if _time(interval[1])<=_time(interval[0]) or any(value<0 for value in delta.values()) or total<=0:return _blocked(result,'CPU ticks reset or interval has no positive total','one monotonic boot epoch with positive aligned CPU tick delta')
    factor=100 if observed.get('unit')=='percent' else 1
    return _numeric(result,observed['value'],factor*delta[target]/total,cpu_interval=interval,delta_ticks=delta,guest_states_excluded=True,comparison='v0.156 per-state delta / eight-state total; guest time not counted twice')


def compare_reference(observed,references):
    result=_comparison(observed,references);name=observed.get('metric');dims=observed.get('dimensions',{})
    if observed.get('source')!='host-os' or not observed.get('node') or not observed.get('node_uid'):
        return _blocked(result,'host source and node lifecycle identity required','host-os source, Node name and immutable Node UID')
    if name in ('system.filesystem.usage','system.filesystem.utilization'):return _filesystem_reference(observed,references,result)
    if name=='system.cpu.utilization':return _cpu_reference(observed,references,result)
    if name=='system.memory.usage':
        if set(dims)!={'state'} or dims.get('state') not in MEMORY_FIELDS:return _blocked(result,'unverified memory formula or extra dimensions','verified Linux memory state; used/cached require feature-gate/derived-field evidence')
        metric='memory.'+MEMORY_FIELDS[dims['state']];ref_dims={};factor=1024;unit='kB'
    elif name in ('system.network.io.receive','system.network.io.transmit'):
        direction=name.rsplit('.',1)[1]
        if set(dims)!={'interface','direction'} or dims.get('direction')!=direction or not dims.get('interface'):return _blocked(result,'complete directional interface identity required','exact node/interface/direction series')
        metric='network.bytes';ref_dims=dims;factor=1;unit='bytes'
    elif name in ('system.disk.io.read','system.disk.io.write'):
        direction=name.rsplit('.',1)[1]
        if set(dims)!={'device','direction'} or dims.get('direction')!=direction or not dims.get('device'):return _blocked(result,'complete directional disk identity required','exact node/device/direction series')
        metric='disk.sectors';ref_dims=None;factor=512;unit='sectors'
    else:return _blocked(result,'no verified reference translation for metric','a pinned source-specific reference contract')
    if observed.get('unit') not in ('bytes','By','B'):return _blocked(result,'source value is not unaggregated bytes','raw byte datapoint rather than a derived API rate')
    rows=[row for row in references if row.get('node')==observed['node'] and row.get('node_uid')==observed['node_uid'] and row.get('source')=='linux-proc' and row.get('metric')==metric and row.get('unit')==unit and (row.get('dimensions')==ref_dims if ref_dims is not None else set(row.get('dimensions',{}))=={'device','major','minor','direction'} and all(row['dimensions'].get(key)==dims[key] for key in ('device','direction')))]
    result['matching_reference_count']=len(rows);result['conversion_factor']=factor
    if name.startswith('system.disk.'):
        devices={(row['dimensions'].get('major'),row['dimensions'].get('minor')) for row in rows}
        if len(devices)!=1 or any(not major or not minor for major,minor in devices):return _blocked(result,'disk name maps to missing or multiple major/minor identities','one attested diskstats device number per node/boot epoch')
        result['reference_device_identity']=list(next(iter(devices)))
    invalid=_host_lifecycle(result,rows)
    if invalid:return invalid
    if observed.get('kind')=='gauge' or (name=='system.memory.usage' and observed.get('kind')=='sum' and observed.get('is_monotonic') is False and _cumulative(observed)):
        selected=[row for row in rows if observed.get('measurement_timestamp') is not None and _same_time(row.get('measurement_timestamp'),observed['measurement_timestamp'])]
        if len(selected)!=1:return _blocked(result,'gauge source timestamp unavailable or ambiguous; acquisition is not measurement time','one independently aligned underlying source timestamp')
        row=selected[0]
        return _numeric(result,observed['value'],float(row['value'])*factor,conversion_factor=factor,matching_references=[row],reference_acquisition_brackets=[[row['acquisition_started_at'],row['acquisition_ended_at']]],comparison='same-source-timestamp gauge conversion')
    return _counter_reference(result,observed,rows,factor)


def compare_lifecycle(observed,snapshots):
    result=_comparison(observed,snapshots);name=observed.get('metric')
    if observed.get('source')!='kube-cluster' or not observed.get('pod_uid') or not observed.get('node') or not observed.get('node_uid') or observed.get('dimensions') or observed.get('unit') not in ('','1','count'):
        return _blocked(result,'complete Kubernetes source/Pod UID/node identity required','native kube-cluster metric, Pod UID, Node UID and all source dimensions')
    candidates=[]
    for snapshot in snapshots:
        if not _valid_bracket(snapshot):continue
        for pod in snapshot.get('items',[]):
            meta=pod.get('metadata',{})
            if (meta.get('namespace'),meta.get('name'),meta.get('uid'),pod.get('spec',{}).get('nodeName'))!=(observed.get('namespace'),observed.get('pod'),observed['pod_uid'],observed['node']):continue
            if snapshot.get('node_uids',{}).get(observed['node'])!=observed['node_uid']:continue
            if name in ('k8s.pod.phase','k8s.pod.status.phase'):value={'Pending':1,'Running':2,'Succeeded':3,'Failed':4,'Unknown':5}.get(pod.get('status',{}).get('phase'),5)
            else:
                containers=[row for key in ('containerStatuses','initContainerStatuses') for row in pod.get('status',{}).get(key,[]) if row.get('name')==observed.get('container')]
                if len(containers)!=1:continue
                container=containers[0]
                if observed.get('container_id') and observed['container_id']!=container.get('containerID'):continue
                if name=='k8s.container.ready':
                    if type(container.get('ready')) is not bool:continue
                    value=int(container['ready'])
                elif name in ('k8s.container.restarts','k8s.container.restart.count'):value=container.get('restartCount')
                else:return _blocked(result,'unsupported lifecycle metric','ready/restart/phase metric contract')
            if value is not None:candidates.append((snapshot,pod,value))
    result['matching_reference_count']=len(candidates)
    if not candidates:return _blocked(result,'matching Pod/container lifecycle unavailable','timestamped Kubernetes Pod inventory with immutable UID, Node UID and container status')
    exact=[item for item in candidates if (observed.get('resource_version') and item[1]['metadata'].get('resourceVersion')==observed['resource_version']) or (observed.get('measurement_timestamp') is not None and _same_time(observed['measurement_timestamp'],item[0].get('measurement_timestamp')))]
    values={item[2] for item in exact}
    if exact and len(values)==1:
        return _numeric(result,observed['value'],next(iter(values)),matching_references=[{'pod_uid':pod['metadata']['uid'],'resource_version':pod['metadata'].get('resourceVersion'),'acquisition_started_at':snapshot['acquisition_started_at'],'acquisition_ended_at':snapshot['acquisition_ended_at'],'value':value} for snapshot,pod,value in exact],comparison='same Pod object version or exact source timestamp; native integer lifecycle mapping')
    if len(values)>1:return _blocked(result,'conflicting lifecycle values at one claimed source identity','one consistent object-version/source-time observation')
    return _blocked(result,'Kubernetes object version/source-time alignment unavailable','collector cached Pod resourceVersion or exact underlying source timestamp; restarts is an upstream gauge that can reset')


def compare_volume(observed,inputs,references):
    result=_comparison(observed,references);field={'k8s.volume.available':'availableBytes','k8s.volume.capacity':'capacityBytes','k8s.pod.volume.available':'availableBytes','k8s.pod.volume.capacity':'capacityBytes'}.get(observed.get('metric'));dims=observed.get('dimensions',{})
    if field is None or observed.get('source')!='kubelet' or not observed.get('collector_uid') or not observed.get('pod_uid') or not observed.get('node_uid') or observed.get('unit') not in ('bytes','By','B') or not dims.get('volume') or set(dims)-{'volume','pvc_name','pvc_namespace','volume_type'}:
        return _blocked(result,'volume source/UID/unit/dimensions unavailable','collector UID, Pod/Node UID, exact volume/PVC dimensions and native byte unit')
    matches=[]
    for row in inputs:
        if row.get('collector_uid')!=observed['collector_uid'] or row.get('node_name')!=observed.get('node') or row.get('status')!=200 or row.get('path','').split('?')[0]!='/stats/summary':continue
        try:
            a,b=row['requested_at_ns']/1e9,row['received_at_ns']/1e9;at=_time(observed['recorded_timestamp'])
            if b<a or not a-10<=at<=b+10:continue
        except (KeyError,TypeError,ValueError):continue
        body=row.get('body',{})
        if body.get('node',{}).get('nodeName')!=observed['node']:continue
        for pod in body.get('pods',[]):
            ref=pod.get('podRef',{})
            if (ref.get('namespace'),ref.get('name'),ref.get('uid'))!=(observed.get('namespace'),observed.get('pod'),observed['pod_uid']):continue
            for volume in pod.get('volume',[]):
                identity={'volume':volume.get('name'),'pvc_name':volume.get('pvcRef',{}).get('name'),'pvc_namespace':volume.get('pvcRef',{}).get('namespace')}
                if identity['pvc_name']:identity['volume_type']='persistentVolumeClaim'
                if any(identity.get(key)!=value for key,value in dims.items()):continue
                if volume.get(field) is not None and volume.get('time') is not None:matches.append((row,volume))
    if len(matches)!=1:return _blocked(result,'missing or ambiguous exact volume receiver input','one receiver response tied to collector UID/Pod UID/node/volume and original source datapoint interval')
    receiver,volume=matches[0]
    selected=[row for row in references if row.get('source')=='kubelet-summary' and row.get('scope')=='volume' and row.get('node')==observed['node'] and row.get('node_uid')==observed['node_uid'] and row.get('pod_uid')==observed['pod_uid'] and row.get('pod')==observed.get('pod') and row.get('namespace')==observed.get('namespace') and row.get('metric')=='fs.'+field and row.get('unit')=='bytes' and all((row.get('dimensions',{}).get(key) if key!='volume_type' else ('persistentVolumeClaim' if row.get('dimensions',{}).get('pvc_name') else None))==value for key,value in dims.items()) and not (set(row.get('dimensions',{}))-set(dims)) and _same_time(row.get('measurement_timestamp'),volume['time']) and _valid_bracket(row)]
    values={row['value'] for row in selected}
    result.update(receiver_value=volume[field],measurement_timestamp=volume['time'],receiver_acquisition_bracket=[receiver['requested_at_ns']/1e9,receiver['received_at_ns']/1e9])
    if len(values)!=1:return _blocked(result,'missing or conflicting same-measurement independent volume reference','independent kubelet sample with exact fs.time, Pod UID/node/volume/PVC identity')
    reference=next(iter(values));result['conversion']={'state':'pass' if float(observed['value'])==float(volume[field]) else 'fail','factor':1}
    _numeric(result,observed['value'],reference,matching_references=selected,comparison='exact receiver fs.time and independently observed same volume measurement')
    if result['conversion']['state']=='fail':result['state']='fail'
    return result



def compare_traffic(name,traffic,verification):
    result={'metric':name,'state':'insufficient','comparison':'existing full-series product-rendered query parity; source population gate remains separate'}
    suffix={'servicegraph.request.count':('exact_request_count_api','request-count','source_to_vm'),
            'servicegraph.request.error.count':('exact_error_count_api','error-count','source_errors_to_vm'),
            'servicegraph.request.duration':('exact_duration_api','duration',None)}.get(name)
    if suffix is None:return result
    key,alias,source_key=suffix
    checks=traffic.get('api_storage_parity',{}).get('checks',{}) or traffic.get('windows',{}).get('whole',{}).get('api_storage_parity',{}).get('checks',{})
    parity=checks.get(key,checks.get(alias,verification.get('counts_path',{}).get(key,{'state':'incomplete'})))
    result['api_storage_parity']=parity
    population=verification.get('source_population',{})
    source=verification.get('counts_path',{}).get(source_key,{}) if source_key else verification.get('duration',{}).get('source_to_vm',{})
    if population.get('matched') is True and source.get('state') in ('pass','fail'):
        source_accuracy=dict(source,population_proof=population)
    else:source_accuracy={'state':'insufficient','reason':'matched source/store population identity not established','required_acquisition':'explicit matching source/partition/window population proof; count equality is insufficient'}
    result['source_accuracy']=source_accuracy
    if parity.get('state')=='fail' or source_accuracy['state']=='fail':result['state']='fail'
    elif traffic.get('independent_valid') is True and parity.get('state')=='pass' and source_accuracy['state']=='pass':result['state']='pass'
    else:result.update(reason='parity and source-population accuracy reported separately; full source accuracy unavailable',required_acquisition=source_accuracy.get('required_acquisition','complete recorder identity and exact query parity'))
    return result


def _attach_alignment(path,observations):
    attestations=list(iter_records(Path(path)/'references/source-acquisition.ndjson'))
    for observation in observations:
        matches=[row for row in attestations if row.get('provenance') and all(row.get(key)==observation.get(key) for key in ('metric','source','node','node_uid','collector_uid')) and str(row.get('raw_timestamp_unix_ns'))==str(observation['raw_point']['timeUnixNano'])]
        if len(matches)==1:
            row=matches[0];observation['alignment_evidence']=row
            for key in ('measurement_timestamp','source_read_bracket','measurement_interval','resource_version'):
                if key in row:observation[key]=row[key]
        elif matches:observation['alignment_error']='ambiguous source-read acquisition attestations'
    return observations


def _kubernetes_observations(path,agents,*,thesis=False):
    path=Path(path);nodes=_read(path/'structured/nodes-before.json').get('items',[])
    node_uids={node['metadata']['name']:node['metadata'].get('uid') for node in nodes}
    roots=[dict(agent) for agent in agents.get('agents',[])]+[{'root':str(path/'structured/gateway'),'collector_uid':_read(path/'structured/gateway/pod.json').get('metadata',{}).get('uid')}]
    result=[]
    for origin in roots:
        for resource,metric,kind,attributes,point in evidence_v4.raw_metrics(Path(origin['root'])/'raw'):
            name=metric.get('name')
            if name not in ('k8s.volume.available','k8s.volume.capacity','k8s.container.ready','k8s.container.restarts','k8s.pod.phase'):continue
            source=attributes.get('karthos_source');node=resource.get('k8s.node.name') or (origin.get('node') if source=='kubelet' else None)
            dims={key:value for key,value in attributes.items() if key!='karthos_source'}
            if name.startswith('k8s.volume.'):
                aliases={'k8s.volume.name':'volume','k8s.pvc.name':'pvc_name','k8s.persistentvolumeclaim.name':'pvc_name','k8s.volume.type':'volume_type','k8s.pvc.namespace':'pvc_namespace'}
                for key,target in aliases.items():
                    if key in resource:dims[target]=resource[key]
                    if key in dims:dims[target]=dims.pop(key)
                if 'pvc_name' in dims and 'pvc_namespace' not in dims:dims['pvc_namespace']=resource.get('k8s.namespace.name')
                dims.setdefault('pvc_name',None);dims.setdefault('pvc_namespace',None)
            if thesis:
                name={'k8s.volume.available':'k8s.pod.volume.available',
                      'k8s.volume.capacity':'k8s.pod.volume.capacity',
                      'k8s.container.restarts':'k8s.container.restart.count',
                      'k8s.pod.phase':'k8s.pod.status.phase'}.get(name,name)
            result.append({'metric':name,'source':source,'value':float(point.get('asDouble',point.get('asInt'))),'unit':metric.get('unit'),
                           **_instrument_contract(metric,kind),
                           'node':node,'node_uid':node_uids.get(node,origin.get('node_uid')),'collector_uid':origin.get('collector_uid'),
                           'namespace':resource.get('k8s.namespace.name'),'pod':resource.get('k8s.pod.name'),'pod_uid':resource.get('k8s.pod.uid'),
                           'container':resource.get('k8s.container.name'),'container_id':resource.get('container.id'),
                           'resource_version':resource.get('k8s.pod.resource_version'),'dimensions':dims,'raw_resource':resource,'raw_attributes':attributes,'raw_point':point,
                           'recorded_timestamp':int(point['timeUnixNano'])/1e9,'measurement_timestamp':None})
    return _attach_alignment(path,result)


def _volume_references(path,agents):
    from ..references.kubelet import read_summary
    result=[];inputs=[]
    for agent in agents.get('agents',[]):
        inputs.extend(iter_records(Path(agent['root'])/'raw/receiver-input.ndjson'))
        node=agent.get('node')
        if not node:continue
        for row in iter_records(Path(path)/('kubelet-'+node+'.ndjson')):
            if row.get('error') or not row.get('summary'):continue
            requested=row.get('requested_at_ns');received=row.get('acquisition_received_at_ns',row.get('received_at_ns'))
            if requested is None or received is None:continue
            for reading in read_summary(row['summary'],node=node,requested_at=requested/1e9,received_at=received/1e9):
                if reading.get('scope')=='volume':result.append(dict(reading,node_uid=agent.get('node_uid')))
    return inputs,result


def _lifecycle_snapshots(path):
    snapshots=list(_read(Path(path)/'extended/kubernetes-inventory.json').get('snapshots',[]))
    snapshots.extend(iter_records(Path(path)/'references/kubernetes-inventory.ndjson'))
    return snapshots


def _references_by_uid(rows, field):
    grouped = defaultdict(list)
    for row in rows:
        uid = row.get(field)
        if isinstance(uid, str) and uid:
            grouped[uid].append(row)
    return grouped


def _extra_resource_checks(path,agents,references,traffic,verification,start,end,*,thesis=False):
    inputs,volumes=_volume_references(path,agents);snapshots=_lifecycle_snapshots(path);checks=[]
    volumes_by_pod = _references_by_uid(volumes, 'pod_uid')
    for observation in _kubernetes_observations(path,agents,thesis=thesis):
        if start is None or end is None or not _time(start)<=observation['recorded_timestamp']<=_time(end):continue
        if observation['metric'].startswith(('k8s.volume.','k8s.pod.volume.')):
            result = compare_volume(observation, inputs, volumes_by_pod.get(observation.get('pod_uid'), ()))
            result['reference_records_available'] = len(volumes)
            checks.append(result)
        else:checks.append(compare_lifecycle(observation,snapshots))
    for name in ('servicegraph.request.count','servicegraph.request.error.count','servicegraph.request.duration'):
        checks.append(compare_traffic(name,traffic,verification))
    return checks,{'volume_reference_records':len(volumes),'receiver_input_records':len(inputs),'kubernetes_inventory_snapshots':len(snapshots)}


def _host_observations(path,agents,*,thesis=False):
    result=[]
    for agent in agents.get('agents',[]):
        for resource,metric,kind,attributes,point in evidence_v4.raw_metrics(Path(agent['root'])/'raw'):
            name=metric.get('name');dims=dict(attributes)
            if not name.startswith('system.'):continue
            source=dims.pop('karthos_source',None)
            canonical=name
            if name=='system.network.io':
                canonical+='.'+str(dims.get('direction',''))
                if 'device' in dims:dims['interface']=dims.pop('device')
            if name=='system.disk.io':canonical+='.'+str(dims.get('direction',''))
            utilization=name in ('system.cpu.utilization','system.filesystem.utilization')
            factor=100 if utilization and not thesis else 1
            result.append(dict(metric=canonical,value=float(point.get('asDouble',point.get('asInt')))*factor,
                unit=('ratio' if thesis else 'percent') if utilization else metric.get('unit'),source=source,
                node=resource.get('k8s.node.name'),node_uid=agent.get('node_uid'),collector_uid=agent.get('collector_uid'),
                dimensions=dims,raw_dimensions=attributes,**_instrument_contract(metric,kind),
                measurement_timestamp=None,recorded_timestamp=int(point['timeUnixNano'])/1e9,
                timestamp_semantics='OTLP collection timestamp; underlying host read is not independently bracketed',raw_point=point,raw_resource=resource))
    return _attach_alignment(path,result)


def _evaluate(ctx,spec,path,traffic_report=None):
    """Assemble independent execution, evidence, and numerical verdicts from disk."""
    from .models import THESIS, THESIS_SCHEDULES
    from ..pilot import container_metrics
    thesis=ctx.manifest.get('schedule') in THESIS_SCHEDULES
    cpu_name,memory_name=container_metrics(thesis)
    path=Path(path);manifest=_read(path/'manifest.json');start=manifest.get('start');end=manifest.get('end')
    traffic=timing_analysis.analyze_window(path) if traffic_report is None else traffic_report
    disruption=spec.get('phase') in ('resource','restart')
    scenario=spec.get('execution_result') or _read(path/'scenario-result.json')
    executed=(scenario.get('state')=='complete' if disruption else
              traffic.get('execution',{}).get('state')=='complete' or traffic.get('generator_contract',{}).get('state')=='available')
    extended=path/'extended';api={file.stem:_read(file) for file in (extended/'api').glob('*.json')}
    stored=list(iter_records(extended/'vm-raw.ndjson'))
    coverage=catalog_coverage(_definitions(ctx,'coverage.yaml'),_read(extended/'catalog.json'),_read(extended/'source-plan.json'),api,stored,start or 0,end or 0,ctx.manifest.get('thresholds',{}))
    try:agents=evidence_v4.agent_evidence(path)
    except (ValueError,TypeError,KeyError,OSError) as exc:agents={'valid':False,'errors':[str(exc)],'agents':[]}
    reference_files=sorted((path/'references').rglob('*.ndjson*'))
    references=[];reference_errors=[]
    for file in reference_files:
        try:references.extend(iter_records(file))
        except (ValueError,TypeError,OSError) as exc:reference_errors.append(str(file)+': '+str(exc))
    checks=[]
    references_by_node = _references_by_uid(references, 'node_uid')
    try:
        for observation in _host_observations(path,agents,thesis=thesis):
            if start is not None and end is not None and _time(start)<=observation['recorded_timestamp']<=_time(end):
                result = compare_reference(observation, references_by_node.get(observation.get('node_uid'), ()))
                result['reference_records_available'] = len(references)
                checks.append(result)
    except (ValueError,TypeError,KeyError,OSError) as exc:reference_errors.append('host observation read: '+str(exc))
    verification=_read(path/'verification.json')
    extras,reference_inventory=_extra_resource_checks(path,agents,references,traffic,verification,start,end,thesis=thesis)
    checks.extend(extras)
    selected=_definitions(ctx,'metrics.yaml')['metrics']
    resource_report=verification.get('resources')
    if resource_report is None and start is not None and end is not None:
        try:
            resource_report=evidence_v4.resource_scores(path,_read(path/'api-nodes.json').get('data',{}).get('nodes',[]),
                _samples(api.get(cpu_name,{})),_samples(api.get(memory_name,{})),[],
                manifest.get('namespace',spec.get('namespace')),_time(start),_time(end),ctx.manifest.get('thresholds',{}),stored,_read(path/'k8s-pods.json').get('items',[]))
        except (ValueError,TypeError,KeyError,OSError) as exc:resource_report={'state':'incomplete','reason':str(exc)}
    resource_report=resource_report or {'state':'incomplete','reason':'container resource evidence unavailable'}
    numerical=_numerical_scope(checks,selected,resource_report,cpu_name,memory_name)
    all_checks=checks
    checks=numerical['checks']
    capture=_read(extended/'capture-status.json');structured=_read(path/'structured/status.json')
    capture_valid=bool(capture.get('state')=='complete' and structured.get('valid') is True and agents.get('valid') and coverage['complete'] and not reference_errors)
    evidence_valid=capture_valid and (disruption or traffic.get('independent_valid') is True)
    report=dict(schema_version=1,spec=spec,manifest=manifest,
                execution={'state':'complete' if executed else 'incomplete','diagnostic_result':scenario if disruption else None},
                evidence={'state':'pass' if evidence_valid else 'incomplete','valid':evidence_valid,'extended_capture':capture,'agent_identities':agents,'reference_errors':reference_errors},
                traffic={'oracle_valid':traffic.get('independent_valid') is True,'expected_disruption':disruption,'report':traffic,'interpretation':'traffic remains diagnostic during resource/restart scenarios' if disruption else 'normal core traffic verifier'},
                numerical_agreement=numerical,
                resources=resource_report,reference_inventory=reference_inventory,coverage=coverage,references={'files':[str(file.relative_to(path)) for file in reference_files],'records':len(references),'errors':reference_errors},
                limitations=['configured resource stimulus is not measured reference truth','acquisition timestamps do not establish aligned gauge measurement times','disk sectors use verified512-byte conversion; CPU/filesystem/disk/network still require aligned source-read identity','fixtures and preparation are not live proof'])
    if thesis:
        report.update(protocol=ctx.manifest['schedule'],verifier_version={'thesis-qualification-v2':'thesis-qualification-v2.1','thesis-qualification-v3':'thesis-qualification-v3.0','thesis-qualification-v4':'thesis-qualification-v4.1','thesis-repair-validation-v1':'thesis-repair-validation-v1.0'}[ctx.manifest['schedule']],
                      product_revision=ctx.manifest['product_pins']['karthos_revision'],
                      thresholds=ctx.manifest.get('thresholds',{}))
    accuracy=[]
    for metric in selected:
        name=metric['name']
        metric_checks=[check for check in all_checks if check.get('metric')==name or check.get('observed',{}).get('metric')==name]
        if name in (cpu_name,memory_name):
            accuracy.append(dict(metric,state={'pass':'pass','fail':'fail'}.get(resource_report.get('state'),'insufficient'),verifier='exact receiver-input resource verifier'))
        elif metric_checks:
            accuracy.append(dict(metric,state='fail' if any(check['state']=='fail' for check in metric_checks) else ('pass' if all(check['state']=='pass' for check in metric_checks) else 'insufficient'),checks=metric_checks))
        else:accuracy.append(dict(metric,state='insufficient',reason='reference identity, lifecycle and source-time alignment not established; observations retained'))
    parity_manifest=_read(path/'extended/parity/manifest.json')
    storage_api={row['metric']:row.get('result',{'state':'insufficient'}) for row in parity_manifest.get('metrics',[]) if row.get('metric')}
    for row in accuracy:row['storage_api_parity']=storage_api.get(row['name'],{'state':'insufficient','reason':'selected product-rendered API/storage parity export unavailable'})
    report['storage_api_parity']={'metrics':storage_api,'independent_accuracy_claimed':False}
    report['selected_accuracy']=accuracy
    from .coverage_report import build as build_coverage
    report['metric_window_coverage']=build_coverage(coverage,traffic,start=start,end=end,checks=all_checks,resources=resource_report)
    report['state']='diagnostic' if executed else 'incomplete'
    ctx.write(path/'gke-evaluation.json',report)
    return report


def evaluate(ctx,spec,path,traffic_report=None):
    """Persist an explicit incomplete result even for unusable exported input."""
    try:return _evaluate(ctx,spec,path,traffic_report)
    except (ValueError,TypeError,KeyError,OSError,EOFError) as exc:
        coverage=catalog_coverage(_definitions(ctx,'coverage.yaml'),{},{},{},[],0,0,{})
        report={'schema_version':1,'spec':spec,'state':'incomplete',
                'execution':{'state':'incomplete'},'evidence':{'state':'incomplete','valid':False,'errors':[str(exc)]},
                'numerical_agreement':{'state':'insufficient','checks':[]},
                'coverage':coverage,'references':{'errors':[str(exc)]},
                'traffic':{'oracle_valid':False,'report':traffic_report,'reason':'unusable per-window exported evidence'}}
        from .coverage_report import build as build_coverage
        report['metric_window_coverage']=build_coverage(coverage,traffic_report or {})
        ctx.write(Path(path)/'gke-evaluation.json',report)
        return report
