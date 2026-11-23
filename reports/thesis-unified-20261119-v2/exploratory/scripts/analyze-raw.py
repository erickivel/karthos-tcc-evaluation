"""Exploratory calculations on sealed, allowlisted captures. No verdicts."""
import json,datetime,statistics,collections,hashlib,sys
from pathlib import Path
BASE=Path('/home/kivel/projects/karthos-evaluation-authorization-20261119/exploratory');OUT=BASE/'outputs';OUT.mkdir(exist_ok=True)
def read(p):return json.loads(p.read_text())
def stamp(t):return float(t) if isinstance(t,(int,float)) else datetime.datetime.fromisoformat(t.replace('Z','+00:00')).timestamp()
def lines(p):
 with p.open() as f:
  for line in f:
   if line.strip():yield json.loads(line)
def series(p):
 result={}
 for row in lines(p):
  key=json.dumps(row['metric'],sort_keys=True);v=result.setdefault(key,{'labels':row['metric'],'points':{}})
  for t,x in zip(row['timestamps'],row['values']):
   if t in v['points']:assert v['points'][t]==x
   v['points'][t]=x
 return list(result.values())
allresults=[]
for label in ['B1','B2','B3','B4','B5','B6','arbitration']:
 p=BASE/'selected'/label;m=read(p/'manifest.json');lo,hi=stamp(m['start']),stamp(m['end']);vm=series(p/'vm-raw.ndjson')
 nodes={x['id']:x for x in read(p/'api-nodes.json')['data']['nodes']};pods=read(p/'k8s-pods.json')['items'];kpods=read(p/'karthos-pods.json')['items'];podmap={x['metadata']['name']:x for x in pods+kpods};ipmap={x['status'].get('podIP'):x['metadata']['name'] for x in pods+kpods if 'status' in x}
 inventory={'app_pods':len(pods),'sidecar_pods':[x['metadata']['name'] for x in pods if any(c['name']=='istio-proxy' for c in x['spec']['containers']+x['spec'].get('initContainers',[]))],'app_container_images':sorted({c['image'] for x in pods for c in x['spec']['containers']})}
 events=[]
 for f in (p/'logs').glob('*__eval-observer.log'):
  for line in f.open():
   if '{' not in line:continue
   x=json.loads(line[line.index('{'):]);x['pod']=f.name.split('__')[1]
   if lo<=stamp(x['observed_at'])<hi:events.append(x)
 unknown=[x for x in events if not(x.get('run_id')==m['run_id'] and x.get('request_id'))]
 groups=collections.Counter((x.get('boundary'),x.get('uri'),x.get('user_agent'),ipmap.get(x.get('remote_addr'),x.get('remote_addr')),str(x.get('grpc_status')),str(x.get('status'))) for x in unknown)
 # Preserve observed classification; probe attribution is assessed separately.
 e6={'observed':len(events),'unknown_count':len(unknown),'unknown_groups':[{'boundary':k[0],'route':k[1],'user_agent':k[2],'origin':k[3],'grpc_status':k[4],'http_status':k[5],'count':v} for k,v in groups.items()]}
 ledger=list(lines(p/'ledger.ndjson'));errors=[x for x in ledger if lo<=stamp(x['started_at'])<hi and int(x.get('status') or 0)>=400];times=sorted(stamp(x['completed_at']) for x in errors)
 edge=lambda s:s['labels'].get('edge_client')=='frontend' and s['labels'].get('edge_server') in ['checkoutservice','checkout']
 counters=[]
 for s in vm:
  l=s['labels'];name=l.get('__name__');iserror=(name=='servicegraph_request_error_count' or name=='servicegraph_request_count' and (l.get('rpc.grpc.status_code','0') not in ['0',''] or l.get('http.response.status_code','').startswith('5')))
  if not edge(s) or not iserror:continue
  pts=sorted((t/1000,v) for t,v in s['points'].items());before=[x for x in pts if x[0]<=lo];inside=[x for x in pts if lo<x[0]<=hi];after=[x for x in pts if x[0]>hi];assert before and inside
  a,b=before[-1],inside[-1];delta=b[1]-a[1];count_before=sum(t<=b[0] for t in times)
  counters.append({'labels':l,'first_retained_sample':pts[0],'pre_window_sample':a,'last_in_window_sample':b,'first_after_window':after[0] if after else None,'all_samples':pts,'sample_interval_median':statistics.median(y[0]-x[0] for x,y in zip(pts,pts[1:])),'resets':sum(y[1]<x[1] for x,y in zip(pts,pts[1:])),'simple_pre_to_last_delta':delta,'errors_completed_after_last_sample':len(times)-count_before,'completed_errors_not_yet_in_counter_at_last_sample':count_before-delta,'first_sample_reaching_all_errors':next((x for x in after if x[1]-a[1]==len(times)),None)})
 e1={'window':[lo,hi],'generator_error_events':len(errors),'generator_error_completed_at':times,'generator_first_error':times[0] if times else None,'generator_last_error':times[-1] if times else None,'error_series':counters,'recorded_queries':[x for x in read(p/'vm-parity/manifest.json')['queries'] if x['api_file'] in ['api-error-count.json','api-request-count.json']]}
 comps=[]
 for s in series(p/'extended/vm-raw.ndjson'):
  l=s['labels'];metric=l.get('__name__');node=nodes.get(l.get('entity_id'),{})
  if metric not in ['container_cpu_usage','container_memory_usage'] or node.get('namespace')!='karthos-system':continue
  vals=[(t/1000,v) for t,v in sorted(s['points'].items()) if lo<=t/1000<hi]
  if not vals:continue
  container=node['labels'].get('k8s.container.name',node['name']);pod=node['labels'].get('k8s.pod.name');host=podmap.get(pod,{}).get('spec',{}).get('nodeName')
  comps.append({'entity_id':node['id'],'container':container,'pod':pod,'node':host,'metric':metric,'unit':l.get('karthos_unit'),'diagnostic':container.startswith('diag-') or pod.startswith('eval-capture'),'mean':statistics.mean(v for t,v in vals),'maximum':max(v for t,v in vals),'samples':len(vals),'first_at':vals[0][0],'last_at':vals[-1][0],'covered_span_seconds':vals[-1][0]-vals[0][0],'method':'arithmetic mean of unique raw recorded samples within [start,end); not time-weighted or causal overhead'})
 # Matched SERVER and CLIENT spans in the same retained ingress stream.
 spans={};conflicts=0
 for f in p.glob('**/raw/traces*'):
  if f.suffix!='.json':continue
  for doc in lines(f):
   for resource in doc.get('resourceSpans',[]):
    attrs={a['key']:next(iter(a['value'].values())) for a in resource.get('resource',{}).get('attributes',[])}
    for scope in resource.get('scopeSpans',[]):
     for x in scope.get('spans',[]):
      key=(x['traceId'],x['spanId']);v={'service':attrs.get('service.name'),'span':x}
      if key in spans and spans[key]!=v:conflicts+=1
      spans[key]=v
 pairs=[]
 for (tid,sid),item in spans.items():
  s=item['span'];start=int(s['startTimeUnixNano'])/1e9
  if item['service'] not in ['checkout','checkoutservice'] or s['kind'] not in [2,'SPAN_KIND_SERVER'] or not s.get('name','').endswith('/PlaceOrder') or not(lo<=start<hi):continue
  parent=spans.get((tid,s.get('parentSpanId')))
  if parent and parent['service']=='frontend' and parent['span']['kind'] in [3,'SPAN_KIND_CLIENT']:
   client=parent['span'];pairs.append({'trace_id':tid,'server_ms':(int(s['endTimeUnixNano'])-int(s['startTimeUnixNano']))/1e6,'client_ms':(int(client['endTimeUnixNano'])-int(client['startTimeUnixNano']))/1e6,'start':start,'end':int(s['endTimeUnixNano'])/1e9})
 e2={'inventory':inventory,'raw_span_count':len(spans),'conflicts':conflicts,'pairs':len(pairs),'server_mean_ms':statistics.mean(x['server_ms'] for x in pairs) if pairs else None,'client_mean_ms':statistics.mean(x['client_ms'] for x in pairs) if pairs else None,'paired_spans':pairs}
 # Quiet endpoints are recorded independently; no data-dependent choice of matching values.
 durations=[]
 for s in vm:
  if edge(s) and s['labels'].get('__name__') in ['servicegraph_request_duration_sum','servicegraph_request_duration_count']:
   pts=sorted((t/1000,v) for t,v in s['points'].items());before=[x for x in pts if x[0]<=lo];after=[x for x in pts if x[0]>hi]
   durations.append({'labels':s['labels'],'pre':before[-1] if before else None,'last':pts[-1],'samples':pts})
 e2['duration_series']=durations
 result={'label':label,'classification':'exploratory, no verdict','E1':e1,'E2':e2,'E5':comps,'E6':e6,'extraction':read(p/'EXTRACTION.json')};(OUT/(label+'.json')).write_text(json.dumps(result,indent=2)+'\n');allresults.append({'label':label,'errors':len(errors),'series':len(counters),'sidecars':len(inventory['sidecar_pods']),'paired_spans':len(pairs),'components':len(comps),'unknown':len(unknown)})
print(json.dumps(allresults),flush=True)
