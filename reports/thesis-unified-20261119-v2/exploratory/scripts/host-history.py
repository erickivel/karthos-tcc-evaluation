"""Post-hoc host bins and lifecycle timeline; deliberately emits no verdict."""
import json,datetime,collections,statistics,pathlib
P=pathlib.Path(__import__('os').environ.get('KARTHOS_ANALYSIS_BASE','/srv/karthos-analysis')+'/exploratory')
def read(p):return json.loads(p.read_text())
def lines(p):
 for l in p.open():
  if l.strip():yield json.loads(l)
def ts(x):return datetime.datetime.fromisoformat(x.replace('Z','+00:00')).timestamp()
p=P/'selected/B1';m=read(p/'manifest.json');lo,hi=ts(m['start']),ts(m['end'])
nodes={x['id']:x for x in read(p/'api-nodes.json')['data']['nodes']};vm=collections.defaultdict(dict)
for x in lines(p/'extended/vm-raw.ndjson'):
 l=x['metric'];n=l['__name__'];metric=None
 if n=='system_cpu_utilization' and l.get('state')=='user':metric='cpu.user.ratio';dim=l.get('cpu')
 if n=='system_memory_usage' and l.get('state')=='free':metric='memory.free.bytes';dim='free'
 if n=='system_network_io_receive' and l.get('network_interface_name')=='ens4':metric='network.ens4.receive.bytes_per_second';dim='ens4'
 if metric:
  node=nodes.get(l.get('entity_id'),{}).get('name',l.get('entity_id'));key=(node,metric,dim)
  for t,v in zip(x['timestamps'],x['values']):
   if t in vm[key]:assert vm[key][t]==v
   vm[key][t]=v
obs=collections.defaultdict(list);ref=collections.defaultdict(list)
def put(target,node,metric,t,v):
 if lo<=t<hi:target[(node,metric,int((t-lo)//60))].append(v)
for (node,metric,dim),pts in vm.items():
 ordered=sorted((t/1000,v) for t,v in pts.items())
 if metric.startswith('network'):
  for (a,x),(b,y) in zip(ordered,ordered[1:]):
   if lo<=a<b<hi and int((a-lo)//60)==int((b-lo)//60) and y>=x:put(obs,node,metric,(a+b)/2,(y-x)/(b-a))
 else:
  for t,v in ordered:put(obs,node,metric,t,v)
for f in (p/'references').glob('*.ndjson'):
 cpu=collections.defaultdict(dict);net={}
 for x in lines(f):
  a=x.get('acquisition_started_at');b=x.get('acquisition_ended_at');t=(a+b)/2;node=x['node'];d=x['dimensions'];metric=x['metric']
  if not lo-60<=t<=hi+60:continue
  if metric=='cpu.time' and d.get('cpu','')!='cpu':cpu[(node,d['cpu'],t)][d['state']]=x['value']
  if metric=='memory.MemFree':put(ref,node,'memory.free.bytes',t,x['value']*1024)
  if metric=='network.bytes' and d.get('interface')=='ens4' and d.get('direction')=='receive':net[(node,t)]=x['value']
 bycpu=collections.defaultdict(list)
 for (node,c,t),states in cpu.items():bycpu[(node,c)].append((t,states))
 for (node,c),pts in bycpu.items():
  pts.sort()
  for (a,x),(b,y) in zip(pts,pts[1:]):
   if not(lo<=a<b<hi and int((a-lo)//60)==int((b-lo)//60)):continue
   keys=set(x)&set(y)-{'guest','guest_nice'};delta={k:y[k]-x[k] for k in keys};total=sum(delta.values())
   if total>0 and min(delta.values())>=0:put(ref,node,'cpu.user.ratio',(a+b)/2,delta['user']/total)
 grouped=collections.defaultdict(list)
 for (node,t),v in net.items():grouped[node].append((t,v))
 for node,pts in grouped.items():
  pts.sort()
  for (a,x),(b,y) in zip(pts,pts[1:]):
   if lo<=a<b<hi and int((a-lo)//60)==int((b-lo)//60) and y>=x:put(ref,node,'network.ens4.receive.bytes_per_second',(a+b)/2,(y-x)/(b-a))
rows=[]
for key in sorted(set(obs)|set(ref)):
 a=obs[key];b=ref[key];av=statistics.mean(a) if a else None;bv=statistics.mean(b) if b else None
 rows.append({'node':key[0],'metric':key[1],'bin':key[2],'start':lo+key[2]*60,'end':lo+(key[2]+1)*60,'observed_mean':av,'reference_mean':bv,'observed_samples':len(a),'reference_samples':len(b),'relative_error_percent':100*abs(av-bv)/abs(bv) if av is not None and bv else None})
(P/'outputs/host-60s.json').write_text(json.dumps({'classification':'exploratory; no verdict','method':'five fixed 60s bins; arithmetic sample means; source-specific dimensions: user CPU per core, free memory, ens4 receive counter rates; reference time is acquisition midpoint, not physical measurement time; only within-bin counter intervals; no interpolation','rows':rows},indent=2)+'\n')
h=P/'inputs/history';api=list(lines(h/'lifecycle-api.ndjson'));refs=list(lines(h/'lifecycle-reference.ndjson'));before=read(h/'snapshot-before.json');after=read(h/'snapshot-after.json')
old=refs[0]['pods'][0]['metadata']['name'];new=next(x for x in refs if x['phase']=='after')['pods'][0]['metadata']['name']
oldids={x['id'] for x in before['nodes'] if x.get('name')==old};newids={x['id'] for x in after['nodes'] if x.get('name')==new}
def edges(s,ids):return [x for x in s['edges'] if x['relation']=='runs_on' and (x['source'] in ids or x['target'] in ids)]
out={'trigger':read(h/'lifecycle-trigger.json'),'restart_record':read(h/'restart-thesis-robustness-full-20261119-v2-checkoutservice.json'),'references':refs,'api_polls':api,'old_pod':old,'new_pod':new,'old_ids':list(oldids),'new_ids':list(newids),'old_runs_on_before':edges(before,oldids),'old_runs_on_after':edges(after,oldids),'new_runs_on_after':edges(after,newids),'before_snapshot':before['snapshot'],'after_snapshot':after['snapshot'],'old_nodes_after':[x for x in after['nodes'] if x['id'] in oldids]}
(P/'outputs/history.json').write_text(json.dumps(out,indent=2)+'\n')
# Duration endpoints fixed by recorded quiet boundaries, not by numerical agreement.
rows=[]
for label in ['B1','B2','B3','B4','B5','B6','arbitration']:
 p=P/'selected'/label;d=read(P/'outputs'/ (label+'.json'));q0=read(p/'quiet-before.json')['completed_at_epoch'];q1=read(p/'quiet-after.json')['completed_at_epoch'];tot=collections.defaultdict(float);parts=[]
 for s in d['E2']['duration_series']:
  a=[x for x in s['samples'] if x[0]<=q0];b=[x for x in s['samples'] if x[0]<=q1]
  if not a or not b:continue
  delta=b[-1][1]-a[-1][1];tot[s['labels']['__name__']]+=delta;parts.append({'labels':s['labels'],'before':a[-1],'after':b[-1],'delta':delta})
 count=tot['servicegraph_request_duration_count'];expected=72 if label=='arbitration' else 360;assert count==expected,(label,count)
 rows.append({'label':label,'quiet_before':q0,'quiet_after':q1,'count':count,'mean_ms':1000*tot['servicegraph_request_duration_sum']/count,'server_span_ms':d['E2']['server_mean_ms'],'client_span_ms':d['E2']['client_mean_ms'],'partitions':parts})
(P/'outputs/durations.json').write_text(json.dumps(rows,indent=2)+'\n')
print('host rows',len(rows),'history polls',len(api))
