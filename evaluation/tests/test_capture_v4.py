import json,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from evaluation import structured_capture as s
from evaluation.tests.test_structured_capture import objects

class RuntimeTests(unittest.TestCase):
 def test_watchdog_has_named_minimal_rbac_and_absolute_deadline(self):
  snapshot={'name':'eval-capture-abc','owner':'abc','namespace':'karthos-system','cleanup_deadline_epoch':time.time()+200,'plans':[],'ownership_marker_uid':'m'}
  items=s.watchdog_objects(snapshot,{'astronomy_locust_image':'python@sha256:x'})
  role=next(x for x in items if x['kind']=='Role')
  self.assertTrue(all(rule.get('resourceNames') for rule in role['rules']))
  self.assertFalse(any('*' in rule['verbs'] for rule in role['rules']))
  job=next(x for x in items if x['kind']=='Job');self.assertEqual(job['spec']['template']['metadata']['annotations']['sidecar.istio.io/inject'],'false');self.assertGreater(job['spec']['activeDeadlineSeconds'],200)
 def test_independent_sampler_failure_retains_received_time_and_unavailable(self):
  with tempfile.TemporaryDirectory() as d:
   sampler=s.FastCapture(SimpleNamespace(namespace='app'),Path(d));sampler.node='node';sampler.mesh_pods=[];sampler.collector_forwards=[]
   with patch('evaluation.structured_capture.r.kube',side_effect=TimeoutError('slow kubelet')):
    sampler._sample_once('kubelet',Path(d)/'kubelet.ndjson')
   row=json.loads((Path(d)/'kubelet.ndjson').read_text());self.assertIn('requested_at',row);self.assertIn('received_at',row);self.assertGreaterEqual(row['received_at_ns'],row['requested_at_ns']);self.assertIn('slow kubelet',row['error'])
 def test_start_failure_restores_and_keeps_cleanup_error(self):
  with tempfile.TemporaryDirectory() as d:
   capture=s.RepairCapture(SimpleNamespace(namespace='app'),Path(d),time.time()+600)
   with patch.object(capture,'_prepare',side_effect=RuntimeError('setup failed')),patch.object(capture,'_restore',return_value={'restored':True}) as restore:
    with self.assertRaisesRegex(RuntimeError,'setup failed'):capture.start()
   restore.assert_called_once();self.assertTrue(capture.stopped);state=json.loads((Path(d)/'structured/status.json').read_text());self.assertFalse(state['valid']);self.assertIn('setup failed',str(state))
 def test_gateway_reader_cpu_headroom_applies_only_to_gke_gateway_capture(self):
  settings={'observer_image':'r@sha256:x','astronomy_locust_image':'p@sha256:x','gateway_reader_cpu_limit_m':750}
  for kind,configured,request,limit in [('gateway',True,'100m','750m'),('agent',True,'5m','100m'),('gateway',False,'5m','100m')]:
   with self.subTest(kind=kind,configured=configured):
    inputs=settings if configured else {k:v for k,v in settings.items() if k!='gateway_reader_cpu_limit_m'}
    plan=s.instrumentation_plan(kind,*objects(kind),'owner',inputs)
    containers=plan['after_workload']['spec']['template']['spec']['containers']
    resources=next(c['resources'] for c in containers if c['name']=='diag-reader')
    self.assertEqual(resources['requests']['cpu'],request)
    self.assertEqual(resources['limits']['cpu'],limit)
 def test_inventory_detects_restart_uid_change_and_empty_required_source(self):
  before={'metadata':{'uid':'u','name':'pod'},'status':{'containerStatuses':[{'name':'agent','restartCount':0}]}}
  after={'metadata':{'uid':'u','name':'pod'},'status':{'containerStatuses':[{'name':'agent','restartCount':1}]}}
  self.assertTrue(s.pod_errors(before,after));after['metadata']['uid']='new';self.assertTrue(any('UID' in x for x in s.pod_errors(before,after)))
 def test_stop_is_idempotent_and_does_not_cancel_watchdog_when_restore_refused(self):
  with tempfile.TemporaryDirectory() as d:
   capture=s.RepairCapture(SimpleNamespace(namespace='app'),Path(d),time.time()+600)
   with patch.object(capture,'_restore',return_value={'restored':False,'error':'user change'}),patch.object(capture,'_cancel_watchdog') as cancel:
    first=capture.stop();second=capture.stop()
   self.assertEqual(first,second);cancel.assert_not_called();self.assertFalse(first['valid'])

class WatchdogAndProxyTests(unittest.TestCase):
 def test_deadline_watchdog_keeps_user_changes_but_restores_other_owned_resource(self):
  from evaluation.capture_watchdog import run, resource_path
  import copy
  cm,work=objects();plan=s.instrumentation_plan('agent',cm,work,'owner',{'observer_image':'r@sha256:x','astronomy_locust_image':'p@sha256:x'})
  snapshot={'owner':'owner','namespace':'karthos-system','ownership_marker_uid':'marker','plans':[plan]}
  resources={resource_path(plan,'configmap','karthos-system'):copy.deepcopy(plan['after_configmap']),resource_path(plan,'workload','karthos-system'):copy.deepcopy(plan['after_workload'])}
  resources[resource_path(plan,'workload','karthos-system')]['spec']['template']['spec']['containers'][0]['image']='user-change'
  class FakeAPI:
   def call(self,path,method='GET',value=None):
    if path.endswith('/karthos-evaluation-owner'):return {'metadata':{'uid':'marker'}}
    item=resources[path]
    if method=='PATCH':
     for op in value:
      if op['op']=='replace' and op['path']=='/data':item['data']=op['value']
    return item
  state=run(snapshot,FakeAPI(),wait=False);self.assertFalse(state['restored']);self.assertFalse(state['resources'][0]['restored']);self.assertFalse(state['resources'][1]['restored']);self.assertEqual(resources[resource_path(plan,'workload','karthos-system')]['spec']['template']['spec']['containers'][0]['image'],'user-change')
 def test_proxy_bounds_full_body_and_never_silently_overwrites(self):
  from evaluation import receiver_input_proxy as proxy
  with tempfile.TemporaryDirectory() as d, patch.object(proxy,'ROOT',Path(d)),patch.object(proxy,'MAX_BYTES',100):
   self.assertTrue(proxy.record({'body':'first'}));original=(Path(d)/'receiver-input.ndjson').read_bytes()
   self.assertFalse(proxy.record({'body':'x'*100}));self.assertEqual((Path(d)/'receiver-input.ndjson').read_bytes(),original);self.assertIn('limit', (Path(d)/'receiver-errors.ndjson').read_text())
 def test_archive_rejects_live_pod_restart_but_preserves_evidence(self):
  import io,tarfile,copy
  with tempfile.TemporaryDirectory() as d:
   cap=s.RepairCapture(SimpleNamespace(namespace='app'),Path(d),time.time()+600);dest=Path(d)/'structured/agent';dest.mkdir(parents=True)
   pod={'metadata':{'name':'karthos-agent-one','uid':'uid'},'spec':{'nodeName':'node'},'status':{'containerStatuses':[{'name':'agent','restartCount':0}]}}
   cap.pods={'agent':pod};after=copy.deepcopy(pod);after['status']['containerStatuses'][0]['restartCount']=1
   def archive(*args,**kwargs):
    with tarfile.open(fileobj=kwargs['stdout'],mode='w') as tar:
     for name,body in [('metrics.json',b'{"resourceMetrics":[{}]}\n'),('receiver-input.ndjson',json.dumps({'collector_uid':'uid','requested_at_ns':1,'received_at_ns':2,'status':200,'body':{}}).encode()+b'\n')]:
      info=tarfile.TarInfo(name);info.size=len(body);tar.addfile(info,io.BytesIO(body))
   with patch.object(cap,'_pod',return_value=after),patch.object(cap,'_get',return_value={'items':[after]}),patch('evaluation.structured_capture.subprocess.run',side_effect=archive):cap._archive('agent')
   self.assertIn('container restarted:agent',cap.state['components']['agent']['errors']);self.assertTrue((dest/'raw/receiver-input.ndjson').exists())

class CaptureSafetyTests(unittest.TestCase):
 def test_existing_capture_is_never_overwritten_on_start(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)/'structured';root.mkdir();status=root/'status.json';status.write_text('original')
   cap=s.RepairCapture(SimpleNamespace(namespace='app'),Path(d),time.time()+600)
   with self.assertRaises(FileExistsError):cap.start()
   self.assertEqual(status.read_text(),'original')
 def test_receiver_gaps_follow_deployed_30_second_interval_by_path(self):
  plan={'original_receiver':{'collection_interval':'30s'}}
  rows=[{'path':'/stats/summary','requested_at_ns':int(t*1e9),'received_at_ns':int((t+.1)*1e9),'collector_uid':'uid','status':200} for t in (1,31,61)]
  self.assertEqual(s.receiver_errors(rows,'uid',plan),[])
  rows[-1]['requested_at_ns']=int(121e9)
  self.assertTrue(any('gap' in x for x in s.receiver_errors(rows,'uid',plan)))

class ArchiveRetryTests(unittest.TestCase):
 def test_retries_capture_only_and_retains_failed_attempt_metadata(self):
  with tempfile.TemporaryDirectory() as d:
   cap=s.RepairCapture(SimpleNamespace(namespace='app'),Path(d),time.time()+600);cap.pods={'agent':{}};(Path(d)/'structured/agent').mkdir(parents=True)
   attempts=[]
   def archive(kind,attempt):
    attempts.append(attempt)
    if attempt==1:raise RuntimeError('file changed as we read it')
    raw=Path(d)/'structured'/kind/('raw-attempt-'+str(attempt));raw.mkdir();(raw/'metrics.json').write_text('{}\n')
    return {'errors':[],'files':[],'raw_directory':str(raw)}
   with patch.object(cap,'_archive_attempt',side_effect=archive,create=True),patch('evaluation.structured_capture.time.sleep'):
    cap._archive('agent')
   self.assertEqual(attempts,[1,2]);self.assertEqual(cap.state['components']['agent']['errors'],[]);self.assertEqual(len(cap.state['components']['agent']['attempts']),2);self.assertIn('file changed',str(cap.state['components']['agent']['attempts'][0]));self.assertTrue((Path(d)/'structured/agent/raw/metrics.json').exists())

class RestoreAtomicityTests(unittest.TestCase):
 def test_partial_install_restores_changed_config_without_replacing_untouched_template(self):
  import copy
  from evaluation.capture_watchdog import run,resource_path
  cm,work=objects();plan=s.instrumentation_plan('agent',cm,work,'owner',{'observer_image':'r@sha256:x','astronomy_locust_image':'p@sha256:x'})
  snapshot={'owner':'owner','namespace':'karthos-system','ownership_marker_uid':'marker','plans':[plan]}
  resources={resource_path(plan,'configmap','karthos-system'):copy.deepcopy(plan['after_configmap']),resource_path(plan,'workload','karthos-system'):copy.deepcopy(plan['before_workload'])};patches=[]
  class FakeAPI:
   def call(self,path,method='GET',value=None):
    if path.endswith('/karthos-evaluation-owner'):return {'metadata':{'uid':'marker'}}
    item=resources[path]
    if method=='PATCH':
     patches.append((path,value))
     for op in value:
      if op['op']=='test':
       actual=item
       for key in op['path'].strip('/').split('/'):actual=actual[key.replace('~1','/').replace('~0','~')]
       if actual!=op['value']:raise ValueError('atomic JSONpatch test failed')
      elif op['path']=='/data':item['data']=op['value']
      elif op['op']=='remove':item['metadata']['annotations'].pop('evaluation.karthos.dev/capture-owner')
    return item
  state=run(snapshot,FakeAPI(),wait=False);self.assertTrue(state['restored']);self.assertEqual(len(patches),1);self.assertEqual(patches[0][0],resource_path(plan,'configmap','karthos-system'));self.assertEqual(resources[resource_path(plan,'configmap','karthos-system')]['data'],cm['data'])

class SourceApplicabilityTests(unittest.TestCase):
 def test_trace_cell_records_mesh_not_applicable_without_invalidating(self):
  with tempfile.TemporaryDirectory() as d:
   sampler=s.FastCapture(SimpleNamespace(namespace='app',source='trace'),Path(d));sampler.mesh_pods=[]
   sampler._sample_once('mesh',Path(d)/'istio-samples.ndjson')
   row=json.loads((Path(d)/'istio-samples.ndjson').read_text());self.assertTrue(row['not_applicable']);self.assertIsNone(row['error']);self.assertEqual(sampler.sampling_errors,[])
 def test_mesh_cell_empty_frontend_checkout_sources_is_invalid(self):
  with tempfile.TemporaryDirectory() as d:
   sampler=s.FastCapture(SimpleNamespace(namespace='app',source='mesh'),Path(d));sampler.mesh_pods=[]
   sampler._sample_once('mesh',Path(d)/'istio-samples.ndjson')
   self.assertTrue(sampler.sampling_errors)

class AdmissionPlanTests(unittest.TestCase):
 def test_server_defaulted_template_is_snapshotted_before_install_and_restores(self):
  import copy
  cm,work=objects();calls=[];applied={};pod={'metadata':{'name':'karthos-agent-p','uid':'p','annotations':{s.OWNER:'x'}},'spec':{'nodeName':'n'}}
  with tempfile.TemporaryDirectory() as d:
   token='w-'+'a'*18
   cap=s.RepairCapture(SimpleNamespace(namespace='app',source='trace'),Path(d),time.time()+600,config={'gateway_restart_token':token});cap.root.mkdir()
   def get(*args,**kwargs):
    if args[1] in applied:return copy.deepcopy(applied[args[1]])
    if 'karthos-evaluation-owner' in args:return {'metadata':{'uid':'marker'}}
    if args[1]=='pods':return {'items':[pod]}
    kind='gateway' if 'gateway' in args[1] or any('gateway' in x for x in args) else 'agent'
    return copy.deepcopy(objects(kind)[0 if args[1]=='configmap' else 1])
   def kube(*args,**kwargs):
    calls.append(args)
    if '--dry-run=server' in args:
     kind='agent' if 'agent' in args[1] else 'gateway';plan=s.instrumentation_plan(kind,*objects(kind),cap.owner,cap.settings);after=copy.deepcopy(plan['after_configmap' if args[1].startswith('configmap') else 'after_workload'])
     if not args[1].startswith('configmap'):
      for c in after['spec']['template']['spec']['containers']:c.update(imagePullPolicy='IfNotPresent',terminationMessagePath='/dev/termination-log',terminationMessagePolicy='File')
      for v in after['spec']['template']['spec']['volumes']:
       if 'configMap' in v:v['configMap']['defaultMode']=420
     return SimpleNamespace(stdout=json.dumps(after))
    if args[0]=='patch' and '--dry-run=server' not in args:
     kind='agent' if 'agent' in args[1] else 'gateway';target='configmap' if args[1].startswith('configmap') else 'workload'
     applied[args[1]]=copy.deepcopy(objects(kind)[0 if target=='configmap' else 1])
     for op in json.loads(args[args.index('-p')+1]):
      if op['op']=='replace' and op['path']=='/data':applied[args[1]]['data']=op['value']
      if op['op']=='replace' and op['path']=='/spec/template':applied[args[1]]['spec']['template']=op['value']
      if op['op']=='add' and op['path']=='/metadata/annotations':applied[args[1]]['metadata']['annotations']=op['value']
    if args[0]=='create':
     snapshot=json.loads((cap.root/'watchdog-snapshot.json').read_text());self.assertEqual(len([c for c in calls if '--dry-run=server' in c]),4)
     for plan in snapshot['plans']:
      self.assertEqual(plan.get('gateway_restart_token'),token if plan['kind']=='gateway' else None)
      for c in plan['after_workload']['spec']['template']['spec']['containers']:self.assertEqual(c['imagePullPolicy'],'IfNotPresent')
     return SimpleNamespace(stdout='{}')
    return SimpleNamespace(stdout='{}')
   with patch.object(cap,'_get',side_effect=get),patch.object(cap,'_pod',return_value=pod),patch.object(cap,'_wait_watchdog_ready'),patch('evaluation.structured_capture.r.kube',side_effect=kube):cap._prepare()
   for plan in cap.plans:
    self.assertTrue(s.restore_patch(plan,'workload',plan['after_workload']))
    altered=copy.deepcopy(plan['after_workload']);altered['spec']['template']['spec']['containers'][0]['unexpected']='user'
    with self.assertRaises(ValueError):s.restore_patch(plan,'workload',altered)

class WindowCoverageTests(unittest.TestCase):
 def test_receiver_requires_summary_and_brackets_measurement(self):
  plan={'original_receiver':{'collection_interval':'30s','extra_metadata_labels':['k8s.volume.type']}}
  def row(path,t):return {'path':path,'requested_at_ns':int(t*1e9),'received_at_ns':int((t+.1)*1e9),'collector_uid':'u','status':200}
  self.assertTrue(any('stats/summary' in x for x in s.receiver_errors([row('/pods',100)],'u',plan,(100,200))))
  rows=[row(path,t) for t in (90,120,150,180,210) for path in ('/stats/summary','/pods')]
  self.assertEqual(s.receiver_errors(rows,'u',plan,(100,200)),[])
  self.assertTrue(any('coverage' in x for x in s.receiver_errors(rows[:2],'u',plan,(100,200))))
 def test_single_metric_point_does_not_prove_full_window_stream(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);file=p/'metrics.json'
   def batch(t):return json.dumps({'resourceMetrics':[{'scopeMetrics':[{'metrics':[{'gauge':{'dataPoints':[{'timeUnixNano':str(int(t*1e9)),'asInt':'1'}]}}]}]}]})+'\n'
   file.write_text(batch(100));self.assertTrue(s.signal_coverage(p,'Metrics',(100,200),30)['errors'])
   file.write_text(''.join(batch(t) for t in (90,120,150,180,210)));self.assertEqual(s.signal_coverage(p,'Metrics',(100,200),30)['errors'],[])
   file.write_text(batch(90)+batch(210));self.assertTrue(any('gap' in e for e in s.signal_coverage(p,'Metrics',(100,200),30)['errors']))
 def test_trace_coverage_uses_request_activity_and_correlated_trace_ids(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);trace='a'*32
   def batch(t,identity):return json.dumps({'resourceSpans':[{'scopeSpans':[{'spans':[{'traceId':identity,'startTimeUnixNano':str(int(t*1e9)),'endTimeUnixNano':str(int((t+.1)*1e9))}]}]}]})+'\n'
   (p/'traces.json').write_text(batch(100,trace)+batch(200,trace))
   self.assertEqual(s.signal_coverage(p,'Spans',(100,200),15,{trace})['errors'],[])
   self.assertTrue(any('correlated' in e for e in s.signal_coverage(p,'Spans',(100,200),15,{'b'*32})['errors']))

class WatchdogStartupTests(unittest.TestCase):
 def test_waits_for_creation_before_waiting_named_pod_ready(self):
  with tempfile.TemporaryDirectory() as d:
   cap=s.RepairCapture(SimpleNamespace(namespace='app'),Path(d),time.time()+600)
   empty=SimpleNamespace(stdout=json.dumps({'items':[]}))
   created=SimpleNamespace(stdout=json.dumps({'items':[{'metadata':{'name':'watchdog-pod','labels':{s.OWNER:cap.owner}},'status':{'phase':'Pending'}}]}))
   calls=[]
   def kube(*args,**kwargs):
    calls.append(args)
    if args[0]=='get':return empty if len(calls)==1 else created
    self.assertEqual(args[0],'wait');self.assertIn('pod/watchdog-pod',args);return SimpleNamespace(stdout='condition met')
   with patch('evaluation.structured_capture.r.kube',side_effect=kube),patch('evaluation.structured_capture.time.sleep'):
    cap._wait_watchdog_ready()
   self.assertEqual([call[0] for call in calls],['get','get','wait'])
 def test_absent_pod_creation_stops_at_study_deadline(self):
  with tempfile.TemporaryDirectory() as d:
   cap=s.RepairCapture(SimpleNamespace(namespace='app'),Path(d),1001)
   clock=[1000.]
   def sleep(seconds):clock[0]+=seconds
   with patch('evaluation.structured_capture.r.kube',return_value=SimpleNamespace(stdout='{"items":[]}')) as kube,patch('evaluation.structured_capture.time.time',side_effect=lambda:clock[0]),patch('evaluation.structured_capture.time.monotonic',side_effect=lambda:clock[0]),patch('evaluation.structured_capture.time.sleep',side_effect=sleep):
    with self.assertRaisesRegex(TimeoutError,'watchdog'):cap._wait_watchdog_ready()
   self.assertTrue(all(call.args[0]=='get' for call in kube.call_args_list))
   self.assertLessEqual(clock[0],1001)

class WatchdogDeletionTests(unittest.TestCase):
 def test_waits_foreground_for_watchdog_pod_before_auxiliary_cleanup(self):
  with tempfile.TemporaryDirectory() as d:
   cap=s.RepairCapture(SimpleNamespace(namespace='app'),Path(d),time.time()+600);cap.watchdog_installed=True
   calls=[]
   def kube(*args,**kwargs):
    calls.append(args)
    if args[0]=='get':return SimpleNamespace(stdout=json.dumps({'metadata':{'uid':args[1]+'-uid','labels':{s.OWNER:cap.owner}}}))
    if args[:2]==('delete','job'):
     self.assertIn('--cascade=foreground',args);self.assertIn('--wait=true',args)
    return SimpleNamespace(stdout='')
   with patch.object(cap,'_cleanup_kube',side_effect=kube):cap._cancel_watchdog()
   deletion=[args[1] for args in calls if args[0]=='delete']
   self.assertEqual(deletion,['job','rolebinding','role','serviceaccount','configmap'])
   self.assertFalse(cap.watchdog_installed)

class SourceCollectorSelectionTests(unittest.TestCase):
 def test_selects_karthos_and_exact_source_containers_but_not_application_agent(self):
  candidates=[('karthos-system','karthos-agent-a','agent'),('karthos-system','karthos-gateway-a','gateway'),('karthos-system','karthos-agent-a','diag-reader'),('app','agent-a','agent'),('app','otel-collector-agent-a','agent')]
  for demo,pod,container in [('boutique','eval-otlp-relay-a','collector'),('astronomy','otel-collector-agent-a','opentelemetry-collector')]:
   with self.subTest(demo=demo),tempfile.TemporaryDirectory() as d:
    capture=s.FastCapture(SimpleNamespace(namespace='app',demo=demo,source='trace'),Path(d))
    selected=[target for target in candidates+ [('app',pod,container)] if capture._collector_target(*target)]
    self.assertEqual(selected,[candidates[0],candidates[1],('app',pod,container)])
 def test_source_joins_existing_five_second_selfmetrics_loop_with_identity(self):
  import contextlib
  from unittest.mock import MagicMock
  with tempfile.TemporaryDirectory() as d:
   def pod(ns,name,containers):return {'metadata':{'namespace':ns,'name':name,'uid':name+'-uid'},'spec':{'nodeName':'node','containers':[{'name':c} for c in containers],'volumes':[{'name':'config','configMap':{'name':'source-config'}}] if ns=='app' else []},'status':{'phase':'Running','containerStatuses':[{'name':c,'restartCount':0} for c in containers]}}
   app=pod('app','eval-otlp-relay-a',['collector']);app_other=pod('app','agent-a',['agent'])
   inventory=[{'items':[app,app_other]},{'items':[pod('karthos-system','karthos-agent-a',['agent','diag-reader']),pod('karthos-system','karthos-gateway-a',['gateway'])]},{'items':[{'metadata':{'name':'node'}}]},{'metadata':{'name':'source-config'},'data':{'config.yaml':'receivers: {}'}}]
   capture=s.FastCapture(SimpleNamespace(namespace='app',demo='boutique',source='trace'),Path(d))
   log=MagicMock();log.status.return_value={}
   with patch('evaluation.capture.r.kube_json',side_effect=inventory),patch('evaluation.capture.PollingLogCapture',return_value=log),patch('evaluation.capture.r.forward',side_effect=lambda target,ns,*args:contextlib.nullcontext('http://'+target.split('/')[1])),patch.object(capture,'_sample'),patch('evaluation.structured_capture.r.kube',return_value=SimpleNamespace(returncode=0,stdout=b'{"node":{"nodeName":"node"},"pods":[]}',stderr=b'')),patch('evaluation.structured_capture.r.urllib.request.urlopen',return_value=contextlib.nullcontext(SimpleNamespace(read=lambda:b'otelcol_receiver_accepted_spans 1\n'))):
    capture.start();capture.stop()
   self.assertEqual(len(capture.collector_forwards),3)
   self.assertEqual(capture.source_collectors[0]['metadata']['uid'],'eval-otlp-relay-a-uid')
   self.assertTrue((Path(d)/'structured/source-collectors/configs/app__source-config.json').exists())
   self.assertEqual(capture.PERIODS['collectors'],5)
   response=contextlib.nullcontext(SimpleNamespace(read=lambda:b'otelcol_exporter_queue_size 0\n'))
   with patch('evaluation.structured_capture.r.urllib.request.urlopen',return_value=response):capture._sample_once('collectors',Path(d)/'collector-metrics.ndjson')
   rows=[json.loads(line) for line in (Path(d)/'collector-metrics.ndjson').read_text().splitlines()]
   self.assertEqual(len(rows),3);source=next(row for row in rows if row['component']=='source')
   self.assertEqual(source['collector_uid'],'eval-otlp-relay-a-uid');self.assertEqual(source['node_name'],'node');self.assertIn('otelcol_exporter_queue_size',source['text'])
 def test_source_restart_is_explicit_capture_failure(self):
  before={'metadata':{'name':'relay','namespace':'app','uid':'uid'},'status':{'containerStatuses':[{'name':'collector','restartCount':0}]}}
  after={'metadata':{'name':'relay','namespace':'app','uid':'uid'},'status':{'containerStatuses':[{'name':'collector','restartCount':1}]}}
  with tempfile.TemporaryDirectory() as d:
   capture=s.RepairCapture(SimpleNamespace(namespace='app',demo='boutique'),Path(d),time.time()+600)
   capture.capture=SimpleNamespace(source_collectors=[before])
   with patch.object(capture,'_get',return_value={'items':[after]}):capture._check_source_collectors()
   self.assertTrue(any('restarted' in str(error) for error in capture.state['errors']))

class SourceCollectorStartupGateTests(unittest.TestCase):
 def run_start(self,directory,mode):
  import contextlib
  from unittest.mock import MagicMock
  def pod(ns,name,container):return {'metadata':{'namespace':ns,'name':name,'uid':name+'-uid'},'spec':{'containers':[{'name':container}],'volumes':[{'name':'config','configMap':{'name':'source-config'}}] if ns=='app' else []},'status':{'phase':'Running'}}
  source=pod('app','eval-otlp-relay-one','collector')
  inventories=[{'items':[] if mode=='missing' else [source]},{'items':[pod('karthos-system','karthos-agent-a','agent'),pod('karthos-system','karthos-gateway-a','gateway')]},{'items':[{'metadata':{'name':'node'}}]},{'metadata':{'name':'source-config'},'data':{'config.yaml':'config'}}]
  capture=s.FastCapture(SimpleNamespace(namespace='app',demo='boutique',source='trace'),Path(directory))
  log=MagicMock();log.status.return_value={}
  def forward(target,*args):
   if mode=='forward' and 'relay' in target:raise RuntimeError('source forward unavailable')
   return contextlib.nullcontext('http://local')
  def configs(*args):
   if len(inventories)==1 and mode=='config':raise RuntimeError('source config unavailable')
   return inventories.pop(0)
  with patch('evaluation.capture.r.kube_json',side_effect=configs),patch('evaluation.capture.PollingLogCapture',return_value=log),patch('evaluation.capture.r.forward',side_effect=forward),patch.object(capture,'_sample') as sample,patch('evaluation.structured_capture.r.urllib.request.urlopen',side_effect=ConnectionRefusedError('metrics disabled')):
   with self.assertRaisesRegex(RuntimeError,'source collector'):capture.start()
  self.assertFalse(sample.called);self.assertTrue(capture.stopped);self.assertTrue(capture.sampling_errors)
  return capture
 def test_missing_source_stops_before_sampling_or_workload(self):
  with tempfile.TemporaryDirectory() as d:self.run_start(d,'missing')
 def test_missing_forward_stops_before_sampling_or_workload(self):
  with tempfile.TemporaryDirectory() as d:self.run_start(d,'forward')
 def test_missing_config_stops_before_sampling_or_workload(self):
  with tempfile.TemporaryDirectory() as d:self.run_start(d,'config')
 def test_connection_refused_stops_before_sampling_or_workload(self):
  with tempfile.TemporaryDirectory() as d:
   self.run_start(d,'metrics')
   status=json.loads((Path(d)/'structured/source-collectors/startup-status.json').read_text())
   self.assertFalse(status['ready']);self.assertIn('metrics disabled',str(status['errors']))

class SourceStartupRestoreTests(unittest.TestCase):
 def test_source_startup_failure_preserves_errors_and_restores_instrumentation(self):
  from unittest.mock import MagicMock
  with tempfile.TemporaryDirectory() as d:
   child=MagicMock();child.start.side_effect=RuntimeError('source collector startup evidence unavailable')
   child.sampling_errors=[{'source':'source-config','error':'missing source config'}]
   child.log_captures=[];child.sampler_threads=[];child.source_collectors=[];child.thread=None;child.kubelet_proxy_lifecycle={'stopped':True};child.log_proxy_lifecycle={'stopped':True};child.mesh_proxy_lifecycle={'stopped':True}
   capture=s.RepairCapture(SimpleNamespace(namespace='app'),Path(d),time.time()+600)
   with patch.object(capture,'_prepare'),patch('evaluation.structured_capture.FastCapture',return_value=child),patch.object(capture,'_restore',return_value={'restored':True}) as restore:
    with self.assertRaisesRegex(RuntimeError,'source collector'):capture.start()
   restore.assert_called_once();child.stop.assert_called_once()
   status=json.loads((Path(d)/'structured/status.json').read_text())
   self.assertFalse(status['valid']);self.assertTrue(status['cleanup']['restored']);self.assertIn('missing source config',str(status['errors']))

class CaptureLogBudgetTests(unittest.TestCase):
 def test_configured_log_budget_is_validated_before_cluster_changes(self):
  for value in (0,-1,True,'huge',1024*1024*1024):
   with self.subTest(value=value),self.assertRaisesRegex(ValueError,'log'):
    s.RepairCapture(SimpleNamespace(namespace='app'),Path('/tmp/unused'),time.time()+600,config={'capture_log_max_bytes':value})
  cap=s.RepairCapture(SimpleNamespace(namespace='app'),Path('/tmp/unused'),time.time()+600,config={'capture_log_max_bytes':256*1024*1024})
  self.assertEqual(cap.status()['capture_bounds']['log_bytes_per_stream'],256*1024*1024)
