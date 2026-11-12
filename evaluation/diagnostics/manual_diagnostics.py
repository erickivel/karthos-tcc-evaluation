"""Bounded, evaluation-owned manual boundary probes; not the formal study scorer."""
import argparse,concurrent.futures,contextlib,datetime,hashlib,http.cookiejar,json,os,statistics,subprocess,sys,tarfile,threading,time,urllib.request,urllib.error,urllib.parse
from pathlib import Path
from evaluation import runner as r,pilot
from evaluation.protocol import Cell
from evaluation.workloads.common import journey,outcome_ok
BASE=Path(__file__).resolve().parent
RUN_ID=os.environ.get('EVAL_DIAGNOSTIC_RUN_ID','root-cause-20261020')
if Path(RUN_ID).name!=RUN_ID or RUN_ID in ('.','..'):raise ValueError('run ID must be one directory name')
OUT=r.ROOT/'results'/RUN_ID
TAG='eval-0e6a607f9193-21e36b8c'

def now():return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')
def write(path,data):r.write(path,data)
def yaml_load(text):return json.loads(subprocess.run(['ruby','-ryaml','-rjson','-e','puts JSON.generate(YAML.safe_load(STDIN.read))'],input=text,text=True,capture_output=True,check=True).stdout)
def kube_json(*args):return r.kube_json(*args)
def cm_config(name):
 cm=kube_json('get','configmap',name,'-n',r.KARTHOS_NAMESPACE);key=next(k for k in cm['data'] if k.endswith(('.yaml','.yml')));return cm,key,yaml_load(cm['data'][key])
def instrument(kind,path,capacity=None):
 path.mkdir(parents=True,exist_ok=True);name='karthos-'+kind;resource=('daemonset/' if kind=='agent' else 'deployment/')+name
 cm,key,config=cm_config(name+'-config');workload=kube_json('get',resource,'-n',r.KARTHOS_NAMESPACE)
 write(path/'configmap-before.json',cm);write(path/'workload-before.json',workload)
 config.setdefault('exporters',{})['file/diagnostic-metrics']={'path':'/diag/metrics.json','flush_interval':'1s','rotation':{'max_megabytes':64,'max_backups':2}}
 for pname,pipe in list(config['service']['pipelines'].items()):
  if pname.startswith('metrics'):pipe.setdefault('exporters',[]).append('file/diagnostic-metrics')
 traces=[pipe for pname,pipe in config['service']['pipelines'].items() if pname.startswith('traces')]
 if kind=='gateway' and traces:
  config['exporters']['file/diagnostic-traces']={'path':'/diag/traces.json','flush_interval':'1s','rotation':{'max_megabytes':128,'max_backups':2}}
  receivers=list(dict.fromkeys(x for pipe in traces for x in pipe['receivers']))
  config['service']['pipelines']['traces/diagnostic']={'receivers':receivers,'processors':[],'exporters':['file/diagnostic-traces']}
 if capacity is not None:config['connectors']['service_graph']['store']['max_items']=capacity
 old_containers=workload['spec']['template']['spec']['containers'];collector=next(c['name'] for c in old_containers if c['name'] in ('agent','gateway'))
 containers=[{'name':collector,'volumeMounts':[{'name':'root-diag','mountPath':'/diag'}]}, {'name':'diag-reader','image':r.CONFIG['observer_image'],'command':['sh','-c','sleep 7200'],'resources':{'requests':{'cpu':'5m','memory':'8Mi'},'limits':{'cpu':'100m','memory':'64Mi'}},'volumeMounts':[{'name':'root-diag','mountPath':'/diag'}]}]
 volumes=[{'name':'root-diag','emptyDir':{'sizeLimit':'512Mi'}}]
 if kind=='agent':
  for receiver,cfg in config['receivers'].items():
   if receiver.replace('_','').startswith('kubeletstats'):
    write(path/'original-kubelet-receiver.json',cfg.copy());cfg['endpoint']='http://127.0.0.1:19050'
  r.apply({'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':'root-diag-proxy','namespace':r.KARTHOS_NAMESPACE,'labels':{'evaluation.karthos.dev/owner':'karthos-evaluation'}},'data':{'receiver_proxy.py':(BASE/'receiver_proxy.py').read_text()}})
  volumes.append({'name':'root-diag-proxy','configMap':{'name':'root-diag-proxy'}})
  containers.append({'name':'diag-kubelet-proxy','image':r.CONFIG['astronomy_locust_image'],'command':['python3','/scripts/receiver_proxy.py'],'env':[{'name':'NODE_IP','valueFrom':{'fieldRef':{'fieldPath':'status.hostIP'}}},{'name':'OTEL_TRACES_EXPORTER','value':'none'}],'resources':{'requests':{'cpu':'10m','memory':'32Mi'},'limits':{'cpu':'250m','memory':'128Mi'}},'volumeMounts':[{'name':'root-diag','mountPath':'/diag'},{'name':'root-diag-proxy','mountPath':'/scripts','readOnly':True}]})
 patch={'spec':{'template':{'metadata':{'annotations':{'evaluation.karthos.dev/diagnostic':now()}},'spec':{'containers':containers,'initContainers':[{'name':'diag-permissions','image':r.CONFIG['observer_image'],'command':['sh','-c','chmod 0777 /diag'],'resources':{'requests':{'cpu':'5m','memory':'8Mi'}},'volumeMounts':[{'name':'root-diag','mountPath':'/diag'}]}],'volumes':volumes}}}}
 write(path/'config-after.json',config);write(path/'workload-patch.json',patch)
 r.kube('patch','configmap',name+'-config','-n',r.KARTHOS_NAMESPACE,'--type=merge','-p',json.dumps({'data':{key:json.dumps(config)}}))
 r.kube('patch',resource,'-n',r.KARTHOS_NAMESPACE,'--type=strategic','-p',json.dumps(patch))
 r.kube('rollout','status',resource,'-n',r.KARTHOS_NAMESPACE,'--timeout=240s',timeout=260)
 if kind=='agent':
  for attempt in range(25):
   pod=pod_for(kind)
   check=r.kube('exec','-n',r.KARTHOS_NAMESPACE,pod,'-c','diag-reader','--','test','-s','/diag/receiver-input.ndjson',check=False,quiet=True,timeout=10)
   if check.returncode==0:break
   time.sleep(3)
  else:raise RuntimeError('diagnostic proxy has no recorded receiver inputs; do not proceed')
 return resource

def pod_for(kind):
 pods=kube_json('get','pods','-n',r.KARTHOS_NAMESPACE)['items'];return next(p['metadata']['name'] for p in pods if p['metadata']['name'].startswith('karthos-'+kind+'-') and p.get('status',{}).get('phase')=='Running' and p['metadata'].get('deletionTimestamp') is None)
def archive(kind,path):
 pod=pod_for(kind);path.mkdir(parents=True,exist_ok=True);file=path/'raw-files.tar'
 with file.open('wb') as out:
  subprocess.run(['kubectl','exec','-n',r.KARTHOS_NAMESPACE,pod,'-c','diag-reader','--','tar','cf','-','-C','/diag','.'],stdout=out,stderr=subprocess.PIPE,check=True,timeout=60)
 with tarfile.open(file) as tar:tar.extractall(path/'raw',filter='data')
 write(path/'pod.json',kube_json('get','pod',pod,'-n',r.KARTHOS_NAMESPACE))
def restore(kind,path):
 name='karthos-'+kind;resource=('daemonset/' if kind=='agent' else 'deployment/')+name
 cm=json.loads((path/'configmap-before.json').read_text());workload=json.loads((path/'workload-before.json').read_text())
 r.kube('patch','configmap',name+'-config','-n',r.KARTHOS_NAMESPACE,'--type=merge','-p',json.dumps({'data':cm['data']}))
 r.kube('patch',resource,'-n',r.KARTHOS_NAMESPACE,'--type=json','-p',json.dumps([{'op':'replace','path':'/spec/template','value':workload['spec']['template']}]))
 r.kube('rollout','status',resource,'-n',r.KARTHOS_NAMESPACE,'--timeout=240s',timeout=260)

class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):return None

def action(base,demo,index,run_id):
 opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),NoRedirect())
 result=[]
 for n,step in enumerate(journey(demo,'checkout-heavy',index,run_id)):
  request_id=f'{run_id}:{index}:{n}';trace_id=hashlib.sha256(request_id.encode()).hexdigest()[:32];parent=hashlib.sha256((request_id+':parent').encode()).hexdigest()[:16]
  headers=dict(step['headers'],**{'X-Evaluation-Run':run_id,'X-Evaluation-Request-ID':request_id,'traceparent':f'00-{trace_id}-{parent}-01'})
  body=step.get('body')
  if isinstance(body,dict):body=json.dumps(body)
  if isinstance(body,str):body=body.encode()
  started=now();tick=time.perf_counter();status=None;error=None;content=''
  try:
   req=urllib.request.Request(base+step['path'],data=body,headers=headers,method=step['method'])
   try:response=opener.open(req,timeout=30)
   except urllib.error.HTTPError as exc:response=exc
   status=response.status;content=response.read().decode(errors='replace')
  except Exception as exc:error=str(exc)
  result.append({'request_id':request_id,'iteration':index,'run_id':run_id,'route':step['route'],'trace_id':trace_id,'started_at':started,'completed_at':now(),'duration_ms':(time.perf_counter()-tick)*1000,'status':status,'expected_status':step['expected_status'],'outcome_ok':outcome_ok(step,status,content),'exception':error,'response_excerpt':content[:400] if error or not outcome_ok(step,status,content) else None})
 return result

def sample_loop(path,stop,cell,collectors):
 node=kube_json('get','nodes')['items'][0]['metadata']['name'];pods=kube_json('get','pods','-n',cell.namespace)['items'];mesh=[p['metadata']['name'] for p in pods if ('checkout' in p['metadata']['name'] or 'frontend' in p['metadata']['name']) and r.has_istio_proxy(p)]
 with (path/'independent-kubelet.ndjson').open('w') as kf,(path/'fast-istio.ndjson').open('w') as mf,(path/'collector-self.ndjson').open('w') as cf:
  seq=0
  while not stop.is_set():
   tick=time.monotonic();before=now()
   try:
    data=json.loads(r.kube('get','--raw',f'/api/v1/nodes/{node}/proxy/stats/summary',quiet=True,timeout=15).stdout);kf.write(json.dumps({'requested_at':before,'received_at':now(),'summary':data})+'\n');kf.flush()
    if seq%2==0:
     for pod in mesh:
      response=r.kube('get','--raw',f'/api/v1/namespaces/{cell.namespace}/pods/{pod}:15090/proxy/stats/prometheus',quiet=True,check=False,timeout=10)
      mf.write(json.dumps({'captured_at':now(),'pod':pod,'text':response.stdout,'error':response.stderr if response.returncode else None})+'\n');mf.flush()
     for kind,base in collectors:
      try:
       text=urllib.request.urlopen(base+'/metrics',timeout=3).read().decode();cf.write(json.dumps({'captured_at':now(),'component':kind,'text':text})+'\n');cf.flush()
      except Exception as exc:cf.write(json.dumps({'captured_at':now(),'component':kind,'error':str(exc)})+'\n')
   except Exception as exc:write(path/'sampling-error.json',{'at':now(),'error':str(exc)})
   seq+=1;stop.wait(max(0,.9-(time.monotonic()-tick)))

def probe(cell,name,shape='steady',count=60):
 path=OUT/cell.demo/name;path.mkdir(parents=True,exist_ok=False);run_id=f'root-cause:{cell.demo}:{name}';started=now();manifest={'demo':cell.demo,'source':cell.source,'name':name,'shape':shape,'journeys':count,'started_at':started,'run_id':run_id}
 manifest['diagnostic_script_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest();write(path/'diagnostic-script.py',Path(__file__).read_text());write(path/'manifest.json',manifest)
 for component in ('agent','gateway'):write(path/('runtime-config-'+component+'.json'),kube_json('get','configmap','karthos-'+component+'-config','-n',r.KARTHOS_NAMESPACE))
 stop=threading.Event()
 with contextlib.ExitStack() as stack:
  frontend='service/frontend' if cell.demo=='boutique' else 'service/frontend-proxy';port=80 if cell.demo=='boutique' else 8080
  base=stack.enter_context(r.forward(frontend,cell.namespace,19180,port));collectors=[]
  for i,kind in enumerate(['agent','gateway']):collectors.append((kind,stack.enter_context(r.forward('pod/'+pod_for(kind),r.KARTHOS_NAMESPACE,19480+i,8888))))
  from evaluation.log_capture import PollingLogCapture
  source_pods=kube_json('get','pods','-n',cell.namespace)['items']
  prefix='eval-otlp-relay-' if cell.demo=='boutique' else 'agent-'
  source_pod=next(x for x in source_pods if x['metadata']['name'].startswith(prefix) and x.get('status',{}).get('phase')=='Running')
  source_container='collector' if cell.demo=='boutique' else 'agent'
  source_capture=PollingLogCapture(cell.namespace,source_pod['metadata']['name'],source_container,path/'source-poll.log',started).start()
  thread=threading.Thread(target=sample_loop,args=(path,stop,cell,collectors));thread.start()
  try:
   for i in range(10):action(base,cell.demo,i,run_id+':warmup')
   time.sleep(35);manifest['workload_start']=now();ledger=[];schedule=time.monotonic()
   with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
    if shape=='burst':
     for begin in range(0,count,10):
      time.sleep(max(0,schedule+(begin//10)*5-time.monotonic()));futures=[pool.submit(action,base,cell.demo,i,run_id) for i in range(begin,min(begin+10,count))]
      for f in futures:ledger+=f.result()
    else:
     for i in range(count):
      time.sleep(max(0,schedule+i*.5-time.monotonic()));ledger+=action(base,cell.demo,i,run_id)
   manifest['workload_end']=now();write(path/'ledger.ndjson',''.join(json.dumps(x)+'\n' for x in ledger));write(path/'outcomes.json',{'requests':len(ledger),'unexpected':sum(not x['outcome_ok'] for x in ledger),'routes':{route:sum(x['route']==route for x in ledger) for route in set(x['route'] for x in ledger)}})
   time.sleep(65)
  finally:
   stop.set();thread.join(timeout=40);write(path/'source-poll-status.json',source_capture.stop())
  api=stack.enter_context(r.forward('deployment/karthos-backend',r.KARTHOS_NAMESPACE,19181,8080));vmservice=next(s['metadata']['name'] for s in kube_json('get','services','-n',r.KARTHOS_NAMESPACE)['items'] if 'victoria' in s['metadata']['name']);vm=stack.enter_context(r.forward('service/'+vmservice,r.KARTHOS_NAMESPACE,19428,8428))
  for metric in ['k8s.container.cpu.usage','k8s.container.memory.usage','servicegraph.request.count','servicegraph.request.error.count','servicegraph.request.duration']:
   params={'metric_name':metric,'from':started,'to':now(),'step':'15s'}
   if metric.startswith('servicegraph'):params['source']='mesh.istio' if cell.source=='mesh' else 'traces.servicegraph'
   try:write(path/('api-'+metric+'.json'),r.api_pages(api,'/api/v1/metrics',params,'samples',pages_dir=path/'api-pages'/metric))
   except Exception as exc:write(path/('api-'+metric+'-error.json'),str(exc))
  write(path/'api-nodes.json',r.api_pages(api,'/api/v1/graph/nodes',{},'nodes',pages_dir=path/'api-pages'/'nodes'))
  query={'match[]':'{__name__=~"k8s_container_(cpu|memory)_usage|servicegraph_request.*"}','start':started,'end':now()}
  try:
   data=urllib.request.urlopen(vm+'/api/v1/export?'+urllib.parse.urlencode(query),timeout=30).read();(path/'vm-raw.ndjson').write_bytes(data)
  except Exception as exc:write(path/'vm-export-error.json',str(exc))
 for kind in ['agent','gateway']:archive(kind,path/kind)
 for ns in [cell.namespace,r.KARTHOS_NAMESPACE]:
  pods=kube_json('get','pods','-n',ns);write(path/(ns+'-pods.json'),pods)
  for pod in pods['items']:
   name=pod['metadata']['name']
   if not any(x in name for x in ['frontend','checkout','relay','gateway','agent']):continue
   for c in pod['spec']['containers']:
    if c['name'].startswith('diag-'):continue
    result=r.kube('logs','-n',ns,name,'-c',c['name'],'--timestamps','--since-time='+started,check=False,quiet=True,timeout=30)
    write(path/'logs'/(ns+'__'+name+'__'+c['name']+'.log'),result.stdout)
    write(path/'logs'/(ns+'__'+name+'__'+c['name']+'.stderr'),result.stderr)
    write(path/'logs'/(ns+'__'+name+'__'+c['name']+'-status.json'),{'returncode':result.returncode,'bytes':len(result.stdout.encode())})
 manifest['finished_at']=now();manifest['sampler_stopped']=not thread.is_alive();write(path/'manifest.json',manifest);r.checksums(path);print('PROBE COMPLETE',path,flush=True)

def deploy(cell):
 path=OUT/cell.demo/'deployment';path.mkdir(parents=True,exist_ok=False)
 r.CONFIG['capture_preroll_seconds']=60
 r.reset_cell(False);r.prepare_source(cell.source);r.create_namespace(cell);r.install_demo(cell,path);pilot.install_observers(cell,path);r.deploy_karthos(cell,path)
 write(path/'source-fingerprint.json',{'runtime_image_tag':TAG,'product_source_hash':r.repository_fingerprint(r.REPO)})

def capacity_change(cell,capacity,level=None):
 path=OUT/cell.demo/('capacity-'+str(capacity)+('-'+level if level else ''));path.mkdir(parents=True,exist_ok=False)
 cm,key,config=cm_config('karthos-gateway-config');write(path/'config-before.json',config)
 config['connectors']['service_graph']['store']['max_items']=capacity
 if level:config['service'].setdefault('telemetry',{}).setdefault('logs',{})['level']=level
 write(path/'config-after.json',config)
 r.kube('patch','configmap','karthos-gateway-config','-n',r.KARTHOS_NAMESPACE,'--type=merge','-p',json.dumps({'data':{key:json.dumps(config)}}))
 r.kube('rollout','restart','deployment/karthos-gateway','-n',r.KARTHOS_NAMESPACE)
 r.kube('rollout','status','deployment/karthos-gateway','-n',r.KARTHOS_NAMESPACE,'--timeout=240s',timeout=260)
 write(path/'pod.json',kube_json('get','pod',pod_for('gateway'),'-n',r.KARTHOS_NAMESPACE))

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('command',choices=['instrument','restore','probe','deploy','capacity']);parser.add_argument('--demo',default='astronomy',choices=['astronomy','boutique']);parser.add_argument('--source',default='mesh',choices=['mesh','trace']);parser.add_argument('--name',default='mesh-manual');parser.add_argument('--journeys',type=int,default=60);parser.add_argument('--shape',default='steady',choices=['steady','burst']);parser.add_argument('--capacity',type=int);parser.add_argument('--log-level',choices=['info','debug']);parser.add_argument('--kind',default='both',choices=['agent','gateway','both']);args=parser.parse_args()
 OUT.mkdir(exist_ok=True);os.environ['IMAGE_TAG']=TAG
 study_file=OUT/'investigation.json'
 if not study_file.exists():
  start=getattr(OUT.stat(),'st_birthtime',OUT.stat().st_ctime);write(study_file,{'started_epoch':start,'deadline_epoch':start+7200,'budget_seconds':7200,'product_source_checkout':str(r.REPO),'image_tag':TAG})
 budget=json.loads(study_file.read_text())['deadline_epoch']-time.time()
 if budget<=0 and args.command!='restore':raise TimeoutError('diagnostic investigation deadline reached')
 r._DEADLINE=time.monotonic()+(300 if args.command=='restore' else budget);r.check_ownership(False);cell=Cell(args.demo,args.source,1)
 if args.command=='capacity':capacity_change(cell,args.capacity,args.log_level)
 elif args.command=='deploy':deploy(cell)
 elif args.command=='probe':
  if not 1<=args.journeys<=600:raise ValueError('bounded diagnostics allow 1 to 600 journeys')
  probe(cell,args.name,args.shape,args.journeys)
 else:
  for kind in (['agent','gateway'] if args.kind=='both' else [args.kind]):
   p=OUT/args.demo/('instrument-'+kind)
   if args.command=='instrument':instrument(kind,p,args.capacity if kind=='gateway' else None)
   else:restore(kind,p)
