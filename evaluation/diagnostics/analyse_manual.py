import json,sys,math,collections,datetime,statistics,hashlib
from pathlib import Path

def rows(path):
 if not path.exists():return []
 return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
def at(s):return datetime.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()
def attrs(items):return {x['key']:next(iter(x.get('value',{}).values()),None) for x in items}
def otlp(path,signal):
 for file in sorted(path.glob('*.json')):
  for doc in rows(file):
   for rm in doc.get('resource'+signal,[]):yield rm

def metrics(path):
 for rm in otlp(path,'Metrics'):
  resource=attrs(rm.get('resource',{}).get('attributes',[]))
  for scope in rm.get('scopeMetrics',[]):
   for metric in scope.get('metrics',[]):
    for kind in ('gauge','sum','histogram'):
     for point in metric.get(kind,{}).get('dataPoints',[]):
      yield resource,metric['name'],metric.get('unit'),kind,attrs(point.get('attributes',[])),point

def resource_analysis(path):
 ns='karthos-eval-'+json.loads((path/'manifest.json').read_text())['demo']
 inputs=rows(path/'agent/raw/receiver-input.ndjson');summaries=[]
 for r in inputs:
  if not r['path'].startswith('/stats/summary'):continue
  containers={}
  for pod in r['body'].get('pods',[]):
   ref=pod.get('podRef',{})
   for c in pod.get('containers',[]):containers[(ref.get('namespace'),ref.get('name'),c.get('name'))]=c
  summaries.append((r['requested_at_ns']/1e9,r['received_at_ns']/1e9,containers))
 independent=collections.defaultdict(list)
 for r in rows(path/'independent-kubelet.ndjson'):
  for pod in r['summary'].get('pods',[]):
   ref=pod['podRef']
   for c in pod.get('containers',[]):
    for kind in ['cpu','memory']:
     data=c.get(kind,{})
     if 'time' in data:independent[((ref['namespace'],ref['name'],c['name']),kind)].append((at(data['time']),c))
 entity={}
 if (path/'api-nodes.json').exists():
  for n in json.loads((path/'api-nodes.json').read_text())['data']['nodes']:
   if n['kind']=='container':
    l=n['labels'];entity[(n.get('namespace',l.get('namespace')),l.get('k8s.pod.name'),l.get('k8s.container.name',n['name']))]=n['id']
 vm={}
 for r in rows(path/'vm-raw.ndjson'):
  m=r['metric'];name=m.get('__name__')
  if name in ['k8s_container_cpu_usage','k8s_container_memory_usage'] and m.get('source')=='kubelet':
   for t,v in zip(r['timestamps'],r['values']):vm[(m.get('entity_id'),name,int(t))]=float(v)
 checks=[];pre_probe_excluded=0;post_scope_excluded=0;manifest=json.loads((path/'manifest.json').read_text());scope_start=at(manifest['started_at']);scope_end=at(manifest['workload_end'])+30
 for res,name,unit,instrument,labels,point in metrics(path/'agent/raw'):
  if name not in ['container.cpu.usage','container.memory.usage'] or res.get('k8s.namespace.name')!=ns:continue
  key=(res.get('k8s.namespace.name'),res.get('k8s.pod.name'),res.get('k8s.container.name'));kind='cpu' if '.cpu.' in name else 'memory';t=int(point['timeUnixNano'])/1e9
  if t<scope_start:
   pre_probe_excluded+=1;continue
  if t>scope_end:
   post_scope_excluded+=1;continue
  candidates=[s for s in summaries if key in s[2]]
  if not candidates:continue
  input_start,input_end,containers=min(candidates,key=lambda s:abs(s[0]-t));gap=abs(input_start-t)
  if gap>5:continue
  c=containers[key];data=c.get(kind,{});raw=float(point.get('asDouble',point.get('asInt',0)));value=raw*1000 if kind=='cpu' else raw
  reference=data.get('usageNanoCores',0)/1e6 if kind=='cpu' else data.get('usageBytes')
  if reference is None:continue
  item=dict(container=list(key),metric=kind,emitted_timestamp=t,input_requested_at=input_start,input_received_at=input_end,kubelet_measurement_timestamp=at(data['time']),kubelet_cache_age_seconds=input_end-at(data['time']),emitted_millicores_or_bytes=value,exact_receiver_reference=reference,exact_input_matches=math.isclose(value,reference,rel_tol=1e-12,abs_tol=1e-8))
  storage_name='k8s_container_'+kind+'_usage';sv=vm.get((entity.get(key),storage_name,int(point['timeUnixNano'])//1000000))
  item.update(stored_value=sv,storage_matches=None if sv is None else math.isclose(value,sv,rel_tol=1e-12,abs_tol=1e-8))
  refs=independent[(key,kind)]
  if refs:
   rt,rc=min(refs,key=lambda x:abs(x[0]-t));rv=rc[kind].get('usageNanoCores',0)/1e6 if kind=='cpu' else rc[kind].get('usageBytes');bound=max(10 if kind=='cpu' else 1048576,abs(rv)*.2)
   item.update(nearest_independent_reference=rv,nearest_independent_measurement_timestamp=rt,nearest_alignment_seconds=abs(rt-t),nearest_reference_within_pilot_tolerance=None if abs(rt-t)>10 else abs(rv-value)<=bound,independent_same_underlying_measurement=abs(rt-item['kubelet_measurement_timestamp'])<.001)
  checks.append(item)
 api_checks=[]
 allowed_ids={eid for key,eid in entity.items() if key[0]==ns}
 for kind in ('cpu','memory'):
  api_file=path/('api-k8s.container.'+kind+'.usage.json')
  if not api_file.exists():continue
  for sample in json.loads(api_file.read_text()).get('data',{}).get('samples',[]):
   t=at(sample.get('timestamp',sample.get('observed_at')))
   if not at(manifest['workload_start'])<=t<=scope_end or sample.get('entity_id') not in allowed_ids:continue
   candidates=[(ts,val) for (eid,name,ts),val in vm.items() if eid==sample['entity_id'] and name=='k8s_container_'+kind+'_usage' and ts/1000<=t]
   if not candidates:continue
   ts,val=max(candidates,key=lambda x:x[0]);api_checks.append({'entity_id':sample['entity_id'],'metric':kind,'evaluation_time':t,'stored_timestamp':ts/1000,'stored_value':val,'api_value':sample['value'],'matches':math.isclose(val,sample['value'],rel_tol=1e-12,abs_tol=1e-8)})
 summary=dict(api_points_checked=len(api_checks),api_storage_mismatches=sum(not x['matches'] for x in api_checks),scope='probe start through 30 seconds after workload end; capture continues to 65 seconds before storage export',scope_start=scope_start,scope_end=scope_end,pre_probe_points_excluded=pre_probe_excluded,post_scope_points_excluded=post_scope_excluded,evidence_state='available' if checks else 'insufficient',points=len(checks),exact_input_mismatches=sum(not x['exact_input_matches'] for x in checks),stored_points=sum(x['storage_matches'] is not None for x in checks),storage_mismatches=sum(x['storage_matches'] is False for x in checks),missing_storage_points=sum(x['storage_matches'] is None for x in checks),nearest_reference_disagreements=sum(x.get('nearest_reference_within_pilot_tolerance') is False for x in checks),cache_age_seconds={'median':statistics.median(x['kubelet_cache_age_seconds'] for x in checks),'max':max(x['kubelet_cache_age_seconds'] for x in checks)} if checks else None)
 return dict(summary=summary,checks=checks,api_checks=api_checks)

def trace_analysis(path):
 ledger=rows(path/'ledger.ndjson');ids={x['trace_id'] for x in ledger};spans={}
 for rm in otlp(path/'gateway/raw','Spans'):
  res=attrs(rm.get('resource',{}).get('attributes',[]));service=res.get('service.name')
  for scope in rm.get('scopeSpans',[]):
   for span in scope.get('spans',[]):
    spans[(span['traceId'],span['spanId'])]=dict(span,service=service)
 servers=[s for s in spans.values() if s['service'] in ('checkout','checkoutservice') and s.get('kind') in (2,'SPAN_KIND_SERVER')]
 clients={(s['traceId'],s['spanId']):s for s in spans.values() if s['service']=='frontend' and s.get('kind') in (3,'SPAN_KIND_CLIENT')}
 selected=[s for s in servers if s['traceId'] in ids]
 paired=[s for s in selected if (s['traceId'],s.get('parentSpanId')) in clients]
 from evaluation.scoring import parse_spans
 parsed={}
 source_files=[path/'source-poll.log'] if (path/'source-poll.log').exists() else list((path/'logs').glob('*.log'))
 for file in source_files:parsed.update(parse_spans(file.read_text(errors='replace')))
 debug_servers=[s for s in parsed.values() if s.get('Trace ID') in ids and s.get('Kind','').lower()=='server' and 'checkout' in (s.get('service') or '').lower() and s.get('Name','').endswith('/PlaceOrder')]
 debug_pairs=[s for s in debug_servers if (s['Trace ID'],s.get('Parent ID')) in parsed and parsed[(s['Trace ID'],s.get('Parent ID'))].get('Kind','').lower()=='client' and parsed[(s['Trace ID'],s.get('Parent ID'))].get('service')=='frontend']
 completed=[]
 for file in (path/'logs').glob('*karthos-gateway*__gateway.log'):
  for line in file.read_text().splitlines():
   pos=line.find('{')
   if pos<0:continue
   try:event=json.loads(line[pos:])
   except ValueError:continue
   if event.get('msg')=='edge completed' and event.get('client_service')=='frontend' and event.get('server_service') in ('checkout','checkoutservice') and event.get('trace_id') in ids:completed.append(event['trace_id'])
 completed_spans=[s for s in paired if s['traceId'] in set(completed)]
 errors=sum(s.get('status',{}).get('code') in (2,'STATUS_CODE_ERROR') or clients[(s['traceId'],s.get('parentSpanId'))].get('status',{}).get('code') in (2,'STATUS_CODE_ERROR') for s in paired)
 return dict(gateway_total_unique_spans=len(spans),all_checkout_server_spans=len(servers),ledger_trace_ids=len(ids),selected_checkout_server_spans=len(selected),selected_complete_pairs=len(paired),selected_unpaired_server_spans=len(selected)-len(paired),source_log_mode='continuous-poll' if (path/'source-poll.log').exists() else 'posthoc-snapshot',source_log_checkout_servers=len(debug_servers),source_log_complete_pairs=len(debug_pairs),source_log_unpaired_checkout_servers=len(debug_servers)-len(debug_pairs),connector_completion_events=len(completed),connector_completed_unique_traces=len(set(completed)),connector_completed_subset_mean_ms=statistics.mean((int(x['endTimeUnixNano'])-int(x['startTimeUnixNano']))/1e6 for x in completed_spans) if completed_spans else None,connector_native_pair_errors=errors,paired_server_mean_ms=statistics.mean((int(x['endTimeUnixNano'])-int(x['startTimeUnixNano']))/1e6 for x in paired) if paired else None,service_names=dict(collections.Counter(s['service'] for s in spans.values())))

def counter_analysis(path):
 m=json.loads((path/'manifest.json').read_text());start,end=at(m['workload_start']),at(m['workload_end']);server='checkoutservice' if m['demo']=='boutique' else 'checkout'
 groups=collections.defaultdict(list)
 for res,name,unit,kind,labels,point in metrics(path/'gateway/raw'):
  if labels.get('client')!='frontend' or labels.get('server')!=server:continue
  if name not in ['traces_service_graph_request_total','traces_service_graph_request_failed_total','traces_service_graph_request_server']:continue
  identity=(name,json.dumps(labels,sort_keys=True));value=float(point.get('asInt',point.get('asDouble',0))) if kind!='histogram' else {'count':float(point['count']),'sum':float(point.get('sum',0))}
  groups[identity].append((int(point['timeUnixNano'])/1e9,value))
 raw=[]
 for (name,identity),points in groups.items():
  points=sorted(points,key=lambda x:x[0]);before=[x for x in points if x[0]<=start];after=[x for x in points if x[0]>=end+30]
  if not before or not after:raw.append({'name':name,'labels':json.loads(identity),'state':'unbracketed'});continue
  a,b=before[-1],after[-1]
  delta={k:b[1][k]-a[1][k] for k in b[1]} if isinstance(b[1],dict) else b[1]-a[1]
  raw.append({'name':name,'labels':json.loads(identity),'state':'bracketed','delta':delta,'baseline_time':a[0],'final_time':b[0]})
 stored=[]
 for r in rows(path/'vm-raw.ndjson'):
  labels=r['metric'];name=labels.get('__name__')
  if labels.get('edge_client')!='frontend' or labels.get('edge_server')!=server:continue
  if name not in ['servicegraph_request_count','servicegraph_request_error_count','servicegraph_request_duration_count','servicegraph_request_duration_sum']:continue
  points=sorted((int(t)/1000,float(v)) for t,v in zip(r['timestamps'],r['values']));before=[x for x in points if x[0]<=start];after=[x for x in points if x[0]>=end+30]
  stored.append({'name':name,'labels':labels,'state':'bracketed' if before and after else 'unbracketed','delta':after[-1][1]-before[-1][1] if before and after else None})
 selfmetrics=collections.defaultdict(list)
 for r in rows(path/'collector-self.ndjson'):
  if r.get('component')!='gateway':continue
  for line in r.get('text','').splitlines():
   if line.startswith('#'):continue
   if any(x in line for x in ['servicegraph_dropped_spans','servicegraph_expired_edges','receiver_refused_spans','receiver_failed_spans']):
    key,_,value=line.rpartition(' ')
    try:selfmetrics[key].append(float(value))
    except ValueError:continue
 return {'raw_connector':raw,'stored':stored,'gateway_counters':{k:{'first':v[0],'last':v[-1],'observed_increase':sum(b-a if b>=a else b for a,b in zip(v,v[1:]))} for k,v in selfmetrics.items()}}

def mesh_analysis(path):
 m=json.loads((path/'manifest.json').read_text());start,end=at(m['workload_start']),at(m['workload_end']);server='checkoutservice' if m['demo']=='boutique' else 'checkout';groups=collections.defaultdict(list)
 import re
 for r in rows(path/'fast-istio.ndjson'):
  if not r.get('pod','').startswith(server+'-'):continue
  for line in r.get('text','').splitlines():
   if line.startswith('istio_requests_total{') and 'reporter="destination"' in line and 'source_workload="frontend"' in line and f'destination_service_name="{server}"' in line:
    key,_,v=line.rpartition(' ');groups[key].append((at(r['captured_at']),float(v)))
 result=[]
 for key,points in groups.items():
  before=[x for x in points if x[0]<=start];after=[x for x in points if x[0]>=end+30]
  if not before or not after:continue
  a,b=before[-1],after[0];status=re.search('grpc_response_status="([^"]*)"',key)
  result.append({'grpc_status':status.group(1) if status else None,'pre_start_slack_seconds':start-a[0],'post_load_drain_seconds':b[0]-end,'delta':b[1]-a[1]})
 return result


if __name__=='__main__':
 p=Path(sys.argv[1]);out={'analyser_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'resource':resource_analysis(p),'trace':trace_analysis(p),'counter':counter_analysis(p),'mesh':mesh_analysis(p)}
 (p/'boundary-analysis.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps({'resource':out['resource']['summary'],'trace':out['trace'],'counter':out['counter'],'mesh':out['mesh']},indent=2))
