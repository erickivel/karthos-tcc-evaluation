"""CPU request admission evidence, not runtime utilization or a scheduler emulator."""
from decimal import Decimal

def milli(value):
 value=str(value)
 result=Decimal(value[:-1]) if value.endswith('m') else Decimal(value)*1000
 if not result.is_finite() or result<0:raise ValueError('invalid CPU quantity')
 return result

def pod_cpu(pod):
 spec=pod.get('spec',{})
 if spec.get('resources',{}).get('requests',{}).get('cpu') is not None:raise ValueError('Pod-level CPU request needs explicit scheduling contract; refusing incomplete capacity calculation')
 def request(c):return milli(c.get('resources',{}).get('requests',{}).get('cpu','0'))
 steady=sum((request(c) for c in spec.get('containers',[])),Decimal(0))
 sidecars=Decimal(0);peak=Decimal(0)
 for c in spec.get('initContainers',[]):
  if c.get('restartPolicy')=='Always':sidecars+=request(c);peak=max(peak,sidecars)
  else:peak=max(peak,sidecars+request(c))
 return max(steady+sidecars,peak)+milli(spec.get('overhead',{}).get('cpu','0'))

def capacity_report(nodes,pods,reserve_millicores=0):
 rows=[]
 for node in nodes.get('items',[]):
  name=node['metadata']['name'];alloc=milli(node['status']['allocatable']['cpu'])
  assigned=[p for p in pods.get('items',[]) if p.get('spec',{}).get('nodeName')==name and p.get('status',{}).get('phase') not in ('Succeeded','Failed')]
  components=[{'namespace':p['metadata'].get('namespace'),'name':p['metadata']['name'],'uid':p['metadata'].get('uid'),'cpu_m':float(pod_cpu(p))} for p in assigned]
  used=sum(pod_cpu(p) for p in assigned)
  rows.append({'node':name,'allocatable_cpu_m':float(alloc),'reserved_cpu_m':float(used),'additional_reserve_cpu_m':reserve_millicores,'remaining_cpu_m':float(alloc-used-reserve_millicores),'pods':components})
 return {'ready':bool(rows) and all(r['remaining_cpu_m']>=0 for r in rows),'nodes':rows,'scope':'assigned nonterminal Pods plus explicit additional reserve; affinity, pending Pods and future rollout demand require separate validation'}

def rollout_capacity(nodes,pods,plans):
 """Conservative per-node peak for admitted capture replacements and surge."""
 import math
 report=capacity_report(nodes,pods);by_node={r['node']:r for r in report['nodes']};problems=[]
 for plan in plans:
  before=plan['before_workload'];after=plan['after_workload'];meta=before['metadata']
  selector=before['spec'].get('selector',{}).get('matchLabels',{})
  if not selector:problems.append('workload selector missing: '+meta['name']);continue
  current=[p for p in pods.get('items',[]) if p.get('metadata',{}).get('namespace')==meta.get('namespace') and p.get('status',{}).get('phase') not in ('Succeeded','Failed') and all(p.get('metadata',{}).get('labels',{}).get(k)==v for k,v in selector.items())]
  if not current or any(p.get('spec',{}).get('nodeName') not in by_node for p in current):
   problems.append('workload placement incomplete: '+meta['name']);continue
  new=pod_cpu({'spec':after['spec']['template']['spec']})
  strategy=after['spec'].get('updateStrategy' if after['kind']=='DaemonSet' else 'strategy',{})
  rolling=strategy.get('rollingUpdate',{})
  replicas=int(after['spec'].get('replicas',1))
  surge=rolling.get('maxSurge',0 if after['kind']=='DaemonSet' else '25%') if strategy.get('type','RollingUpdate')=='RollingUpdate' else 0
  surge=math.ceil(replicas*float(surge[:-1])/100) if isinstance(surge,str) and surge.endswith('%') else int(surge)
  placements={p['spec']['nodeName'] for p in current}
  for node in placements:
   members=[p for p in current if p['spec']['nodeName']==node]
   delta=sum(max(Decimal(0),new-pod_cpu(p)) for p in members)
   # All surge replicas may land on one eligible existing node; no optimistic bin packing.
   extra=delta+new*surge
   by_node[node]['remaining_cpu_m']-=float(extra)
   by_node[node].setdefault('planned_capture',[]).append({'workload':meta['name'],'steady_delta_cpu_m':float(delta),'surge_cpu_m':float(new*surge),'additional_cpu_m':float(extra)})
 report.update(ready=not problems and all(r['remaining_cpu_m']>=0 for r in by_node.values()),problems=problems,
  scope='live assigned reservations plus admitted capture steady deltas and conservative rollout surge; no utilization inference')
 return report

def eligible(node,spec):
 meta=node['metadata'];labels=meta.get('labels',{})
 if spec.get('nodeName') and spec['nodeName']!=meta['name']:return False
 if any(labels.get(k)!=v for k,v in spec.get('nodeSelector',{}).items()):return False
 def expression(e,values):
  key=e['key'];op=e['operator'];actual=values.get(key);wanted=e.get('values',[])
  if op=='In':return actual in wanted
  if op=='NotIn':return actual not in wanted
  if op=='Exists':return key in values
  if op=='DoesNotExist':return key not in values
  if op in ('Gt','Lt'):
   try:return int(actual)>int(wanted[0]) if op=='Gt' else int(actual)<int(wanted[0])
   except (ValueError,TypeError,IndexError):return False
  raise ValueError('unsupported node selector operator')
 required=spec.get('affinity',{}).get('nodeAffinity',{}).get('requiredDuringSchedulingIgnoredDuringExecution')
 if required and not any(term and all(expression(e,labels) for e in term.get('matchExpressions',[])) and all(expression(e,{'metadata.name':meta['name']}) for e in term.get('matchFields',[])) for term in required.get('nodeSelectorTerms',[])):return False
 for taint in node.get('spec',{}).get('taints',[]):
  if taint.get('effect') not in ('NoSchedule','NoExecute'):continue
  if not any((not t.get('effect') or t['effect']==taint['effect']) and ((t.get('operator','Equal')=='Exists' and (not t.get('key') or t['key']==taint['key'])) or (t.get('operator','Equal')=='Equal' and t.get('key')==taint['key'] and t.get('value','')==taint.get('value',''))) for t in spec.get('tolerations',[])):return False
 return True

def additions_capacity(nodes,pods,objects):
 report=capacity_report(nodes,pods);by_node={r['node']:r for r in report['nodes']};problems=[]
 for obj in objects:
  spec=obj['spec'] if obj['kind']=='Pod' else obj.get('spec',{}).get('template',{}).get('spec')
  if spec is None:continue
  if spec.get('topologySpreadConstraints') or any(spec.get('affinity',{}).get(k) for k in ('podAffinity','podAntiAffinity')):
   problems.append('unsupported inter-Pod scheduling constraints: '+obj['metadata']['name']);continue
  targets=[n for n in nodes['items'] if eligible(n,spec)]
  if not targets:problems.append('no eligible node: '+obj['metadata']['name']);continue
  demand=pod_cpu({'spec':spec})*int(obj.get('spec',{}).get('replicas',obj.get('spec',{}).get('parallelism',1)))
  for node in targets:
   r=by_node[node['metadata']['name']];r['remaining_cpu_m']-=float(demand)
   r.setdefault('planned_additions',[]).append({'name':obj['metadata']['name'],'cpu_m':float(demand)})
 report.update(ready=not problems and all(r['remaining_cpu_m']>=0 for r in report['nodes']),problems=problems,
  scope='admitted new Pod specs; conservative peak on each eligible node, including selectors/required node affinity/taints; no optimistic bin packing')
 return report

def verify_additions(ctx,objects):
 import copy,hashlib,json,time
 from .models import write
 admitted=[]
 for obj in objects:
  template=obj if obj.get('kind')=='Pod' else obj.get('spec',{}).get('template')
  if not template:continue
  pod={'apiVersion':'v1','kind':'Pod','metadata':copy.deepcopy(template.get('metadata',{})),'spec':copy.deepcopy(template['spec'])}
  pod['metadata'].update(name='eval-capacity-'+hashlib.sha256(obj['metadata']['name'].encode()).hexdigest()[:16],namespace=obj['metadata'].get('namespace','default'))
  result=ctx.kube('create','--dry-run=server','-f','-','-o','json',input_text=json.dumps(pod))
  data=json.loads(result.stdout)
  item=copy.deepcopy(obj)
  if item['kind']=='Pod':item['spec']=data['spec']
  else:item['spec']['template']['spec']=data['spec']
  admitted.append(item)
 if not admitted:return
 nodes=ctx.json('get','nodes');pods=ctx.json('get','pods','-A');report=additions_capacity(nodes,pods,admitted)
 path=ctx.result/'infrastructure/capacity'/str(time.time_ns())
 write(path/'nodes.json',nodes);write(path/'pods.json',pods);write(path/'admitted.json',admitted);write(path/'report.json',report)
 if not report['ready']:raise ValueError('planned workload CPU/placement admission invalid; see '+str(path))
