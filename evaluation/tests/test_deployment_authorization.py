import copy
import importlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

EMAIL='controller@example.iam.gserviceaccount.com'
LABELS={'evaluation.karthos.dev/owner':'gke-qualification-v1','evaluation.karthos.dev/run-id':'run-a'}

def binding():
 return {'metadata':{'name':'karthos-evaluation-controller-bootstrap','uid':'binding-uid','labels':dict(LABELS),'annotations':{'evaluation.karthos.dev/cluster-uid':'cluster-uid'}},'roleRef':{'apiGroup':'rbac.authorization.k8s.io','kind':'ClusterRole','name':'cluster-admin'},'subjects':[{'kind':'User','apiGroup':'rbac.authorization.k8s.io','name':EMAIL}]}

class Cluster:
 def __init__(self,root):
  self.root=self.work=self.result=Path(root);self.run_id='run-a';self.deadline=2000
  self.config={'execution_profile':'thesis-robustness-full-v1','limits':{'cleanup_reserve_seconds':100}}
  self.state={'terraform':{'foundation':{'controller_service_account':EMAIL}},'resources':[{'kind':'cluster','name':'projects/p/locations/z/clusters/c','uid':'cluster-uid'}]}
  self.bootstrap=binding();self.username=EMAIL;self.allowed=True;self.objects={};self.calls=[];self.events=[];self.fail_delete=False;self.malformed_access=False;self.changed_uid=False
 def event(self,name,**kwargs):self.events.append((name,kwargs))
 def write(self,path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))
 def kube(self,*args,**kw):
  self.calls.append((args,kw));data=json.loads(kw['input_text']) if 'input_text' in kw else None
  if args[:3]==('get','clusterrolebinding','karthos-evaluation-controller-bootstrap'):
   return SimpleNamespace(stdout=json.dumps(self.bootstrap) if self.bootstrap else '',returncode=0)
  if args[:2]==('create','--raw'):
   if 'selfsubjectreviews' in args[2]:out={'status':{'userInfo':{'username':self.username}}}
   elif 'selfsubjectaccessreviews' in args[2]:out={'status':{} if self.malformed_access else {'allowed':self.allowed,'denied':not self.allowed}}
   else:
    out=copy.deepcopy(data);out['metadata'].update(uid='probe-'+str(len(self.objects)),resourceVersion='1');self.objects[(args[2].split('/')[-1],out['metadata']['name'])]=out
   return SimpleNamespace(stdout=json.dumps(out),returncode=0)
  if args[0]=='patch':
   out=self.objects[(args[1],args[2])];ops=json.loads(args[args.index('-p')+1]);assert any(x.get('path')=='/metadata/uid' and x.get('value')==out['metadata']['uid'] for x in ops);out['metadata']['resourceVersion']='2'
   return SimpleNamespace(stdout=json.dumps(out),returncode=0)
  if args[0]=='replace':
   key=({'ClusterRole':'clusterroles','Role':'roles','ClusterRoleBinding':'clusterrolebindings','RoleBinding':'rolebindings'}[data['kind']],data['metadata']['name']);out=self.objects[key];assert out['metadata']['uid']==data['metadata']['uid'];assert out['metadata']['resourceVersion']==data['metadata']['resourceVersion'];out=copy.deepcopy(data);out['metadata']['resourceVersion']='3';self.objects[key]=out
   return SimpleNamespace(stdout=json.dumps(out),returncode=0)
  if args[0]=='get':
   out=copy.deepcopy(self.objects.get((args[1],args[2])));
   if out and self.changed_uid:out['metadata']['uid']='foreign-uid'
   return SimpleNamespace(stdout=json.dumps(out) if out else '',returncode=0)
  if args[:2]==('delete','--raw'):
   if self.fail_delete:raise RuntimeError('delete denied')
   key=tuple(args[2].split('/')[-2:]);out=self.objects[key];assert data['preconditions']=={'uid':out['metadata']['uid'],'resourceVersion':out['metadata']['resourceVersion']};del self.objects[key]
   return SimpleNamespace(stdout='{}',returncode=0)
  if args[0]=='wait':return SimpleNamespace(stdout='',returncode=0)
  raise AssertionError(args)

class AuthorizationTests(unittest.TestCase):
 def module(self):
  try:return importlib.import_module('evaluation.gke.deployment_authorization')
  except ImportError:self.fail('runtime authorization gate missing')
 def run_gate(self,ctx,**kwargs):
  with patch('evaluation.gke.provision.assert_project_lease'),patch('evaluation.gke.provision.verify_resource',return_value={'uid':'cluster-uid'}):
   return self.module().verify(ctx,1,clock=lambda:1000,**kwargs)
 def test_success_checks_real_principal_and_removes_every_probe(self):
  with tempfile.TemporaryDirectory() as d:
   c=Cluster(d);r=self.run_gate(c);self.assertTrue(r['verified']);self.assertTrue(r['probe_cleanup_verified']);self.assertEqual(c.objects,{});self.assertEqual(r['cluster_uid'],'cluster-uid');self.assertTrue(any(a[0]=='replace' for a,k in c.calls));self.assertTrue(any(a[0]=='patch' for a,k in c.calls));self.assertEqual(len(r['probe_objects']),4);self.assertEqual(sum(a[0]=='delete' for a,k in c.calls),4)
 def test_numeric_identity_is_fatal_before_probe(self):
  with tempfile.TemporaryDirectory() as d:
   c=Cluster(d);c.username='123456789'
   with self.assertRaisesRegex(RuntimeError,'identity'):self.run_gate(c)
   self.assertEqual(c.objects,{})
 def test_binding_for_different_cluster_is_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   c=Cluster(d);c.bootstrap['metadata']['annotations']['evaluation.karthos.dev/cluster-uid']='old-cluster'
   with self.assertRaisesRegex(RuntimeError,'binding'):self.run_gate(c)
   self.assertFalse(any('selfsubjectreviews' in a for a,k in c.calls))
 def test_binding_wrong_owner_or_subject_is_rejected(self):
  for change in ('owner','subject'):
   with self.subTest(change=change),tempfile.TemporaryDirectory() as d:
    c=Cluster(d)
    if change=='owner':c.bootstrap['metadata']['labels']['evaluation.karthos.dev/run-id']='foreign'
    else:c.bootstrap['subjects'][0]['name']='other@example.com'
    with self.assertRaisesRegex(RuntimeError,'binding'):self.run_gate(c)
 def test_access_denial_and_malformed_response_are_fatal(self):
  for malformed in (False,True):
   with self.subTest(malformed=malformed),tempfile.TemporaryDirectory() as d:
    c=Cluster(d);c.allowed=False;c.malformed_access=malformed
    with self.assertRaisesRegex(RuntimeError,'permission'):self.run_gate(c)
    self.assertEqual(c.objects,{})
 def test_failed_probe_cleanup_is_not_a_pass(self):
  with tempfile.TemporaryDirectory() as d:
   c=Cluster(d);c.fail_delete=True
   with self.assertRaisesRegex(RuntimeError,'cleanup'):self.run_gate(c)
   self.assertTrue(c.objects)
 def test_changed_probe_uid_is_not_deleted(self):
  with tempfile.TemporaryDirectory() as d:
   c=Cluster(d);c.changed_uid=True
   with self.assertRaisesRegex(RuntimeError,'cleanup'):self.run_gate(c)
   self.assertFalse(any(a[0]=='delete' for a,k in c.calls))
 def test_missing_binding_wait_is_bounded_by_cleanup_cutoff(self):
  with tempfile.TemporaryDirectory() as d:
   c=Cluster(d);c.bootstrap=None;c.deadline=1101;now=[1000]
   def sleep(seconds):now[0]+=seconds
   with patch('evaluation.gke.provision.assert_project_lease'),patch('evaluation.gke.provision.verify_resource',return_value={'uid':'cluster-uid'}):
    with self.assertRaisesRegex(RuntimeError,'bootstrap'):self.module().verify(c,1,clock=lambda:now[0],sleep=sleep)
   self.assertLessEqual(now[0],1001);self.assertTrue(c.events)
 def test_foreign_cluster_or_lease_blocks_before_kubernetes(self):
  for failure in ('lease','cluster'):
   with self.subTest(failure=failure),tempfile.TemporaryDirectory() as d:
    c=Cluster(d)
    with patch('evaluation.gke.provision.assert_project_lease',side_effect=ValueError('lease mismatch') if failure=='lease' else None),patch('evaluation.gke.provision.verify_resource',return_value=None):
     with self.assertRaises(RuntimeError):self.module().verify(c,1,clock=lambda:1000)
    self.assertEqual(c.calls,[])
 def test_binding_read_denial_is_not_polled(self):
  with tempfile.TemporaryDirectory() as d:
   c=Cluster(d);calls=[]
   def denied(*args,**kwargs):calls.append(args);raise RuntimeError('Error from server (Forbidden): binding read denied')
   c.kube=denied
   with self.assertRaisesRegex(RuntimeError,'Forbidden'):self.run_gate(c,sleep=lambda _:self.fail('authorization denial must not wait'))
   self.assertEqual(len(calls),1)
 def test_missing_binding_can_arrive_during_explicit_handshake(self):
  with tempfile.TemporaryDirectory() as d:
   c=Cluster(d);c.bootstrap=None;sleeps=[]
   def sleep(seconds):sleeps.append(seconds);c.bootstrap=binding()
   r=self.run_gate(c,sleep=sleep);self.assertTrue(r['verified']);self.assertEqual(sleeps,[5])
 def test_probe_create_failure_still_cleans_confirmed_objects(self):
  with tempfile.TemporaryDirectory() as d:
   c=Cluster(d);original=c.kube
   def fail(*args,**kwargs):
    if args[:2]==('create','--raw') and args[2].endswith('/clusterrolebindings'):raise RuntimeError('probe create forbidden')
    return original(*args,**kwargs)
   c.kube=fail
   with self.assertRaisesRegex(RuntimeError,'probe create forbidden'):self.run_gate(c)
   self.assertEqual(c.objects,{})
 def test_partial_create_response_lost_is_recovered_for_cleanup(self):
  with tempfile.TemporaryDirectory() as d:
   c=Cluster(d);original=c.kube
   def lost(*args,**kwargs):
    result=original(*args,**kwargs)
    if args[:2]==('create','--raw') and args[2].endswith('/clusterrolebindings'):raise RuntimeError('transport reply lost')
    return result
   c.kube=lost
   with self.assertRaisesRegex(RuntimeError,'transport reply lost'):self.run_gate(c)
   self.assertEqual(c.objects,{})
 def test_legacy_profile_does_not_probe(self):
  with tempfile.TemporaryDirectory() as d:
   c=Cluster(d);c.config['execution_profile']='legacy';r=self.module().verify(c,1);self.assertEqual(r['state'],'not-required');self.assertEqual(c.calls,[])

class CommandAuthorizationTests(unittest.TestCase):
 def test_full_profile_permission_denial_has_nonretryable_type(self):
  from evaluation.gke.cloud_identity import command_failure
  with tempfile.TemporaryDirectory() as d:
   c=Cluster(d);error=command_failure(c,['helm','upgrade','karthos'],SimpleNamespace(returncode=1,stderr='clusterrolebindings "karthos-agent" is forbidden: User "123" cannot patch resource "clusterrolebindings"'))
   self.assertEqual(type(error).__name__,'AuthorizationCommandError')
   self.assertTrue(list((Path(d)/'command-diagnostics').glob('*.stderr')))
 def test_ordinary_application_403_and_legacy_errors_remain_retryable(self):
  from evaluation.gke.cloud_identity import command_failure
  with tempfile.TemporaryDirectory() as d:
   c=Cluster(d)
   for args,message in [(['curl','http://frontend'],'HTTP403 forbidden'),(['kubectl','logs','frontend'],'application request returned 403 Forbidden'),(['helm','upgrade','karthos'],'timed out waiting for deployment')]:
    self.assertIs(type(command_failure(c,args,SimpleNamespace(returncode=1,stderr=message))),RuntimeError)
   c.config['execution_profile']='legacy';self.assertIs(type(command_failure(c,['kubectl','get','pods'],SimpleNamespace(returncode=1,stderr='Error from server (Forbidden): cannot list pods'))),RuntimeError)
 def test_authorization_failure_exports_first_attempt_and_does_not_retry(self):
  import time
  from evaluation.gke import cloud_identity, recoverable_run as recovery
  from evaluation.gke.models import write
  error_type=getattr(cloud_identity,'AuthorizationCommandError',None)
  self.assertIsNotNone(error_type,'typed authorization error missing')
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);uploaded=[];setup=[]
   spec={'id':'smoke','repeat':1,'phase':'smoke','cell':'boutique-trace','demo':'boutique','source':'trace','generator':'k6','scenario':'matched','duration_seconds':60,'rate':2}
   ctx=SimpleNamespace(result=root,run_id='unit-check',manifest={'matrix':[spec]},config={'execution_profile':'thesis-robustness-full-v1','execution_segment':'all','limits':{'cleanup_reserve_seconds':10}},deadline=time.time()+3600,state={'resources':[]},write=write,event=lambda *a,**k:None)
   class Driver:
    def __init__(self):self.paths={};self.nodes=[]
    def start_repeat(self,*a):pass
    def setup(self,a):setup.append(a['id']);raise error_type('RBAC patch forbidden')
    def upload(self,a,r):uploaded.append(a['id']);return {'verified':True,'uri':'gs://test/'+a['id']}
    def finish(self):return {'cluster_cleanup':{'residual':[]}}
   class Diagnostics:
    def __init__(self,c,path,**kw):self.path=path
    def start(self):self.path.mkdir(parents=True)
    def stop(self):write(self.path/'diagnostics-status.json',{'stopped':True});return {'stopped':True}
   with patch('evaluation.gke.execution_diagnostics.ExecutionDiagnostics',Diagnostics),patch('evaluation.gke.provision.require_context'),patch('evaluation.gke.provision.assert_project_lease'),patch.object(recovery,'transition',return_value={'safe':True}),patch.object(recovery,'_checkpoint',return_value={'verified':True}),patch('evaluation.gke.thesis.claim_matrix',return_value={'claims':[]}):
    report=recovery.execute(ctx,Driver())
   self.assertEqual(setup,['smoke-a1']);self.assertEqual(uploaded,['smoke-a1']);self.assertEqual(report['attempts'],1);self.assertTrue(report['records'][0]['retry_policy_complete']);self.assertIn('authorization',report['failure']['message'])
 def test_driver_checks_authorization_before_node_or_application_preflight(self):
  from evaluation.gke.run import LiveDriver
  from evaluation.gke.deployment_authorization import AuthorizationFailure
  ctx=SimpleNamespace()
  with patch('evaluation.gke.provision.create_cluster'),patch('evaluation.gke.deployment_authorization.verify',side_effect=AuthorizationFailure('denied')) as gate,patch('evaluation.gke.setup.preflight') as preflight:
   with self.assertRaises(AuthorizationFailure):LiveDriver(ctx).start_repeat(1)
  gate.assert_called_once_with(ctx,1);preflight.assert_not_called()

class AuthorizationReviewRegressionTests(unittest.TestCase):
 def test_env_assignment_wrapper_keeps_control_denial_nonretryable(self):
  from evaluation.gke.cloud_identity import command_failure
  with tempfile.TemporaryDirectory() as d:
   c=Cluster(d)
   for prefix in (['env','KUBECONFIG=/private/kube'],['/usr/bin/env','KUBECONFIG=/private/kube','CLOUDSDK_CONFIG=/private/gcloud']):
    err=command_failure(c,[*prefix,'gcloud','container','clusters','get-credentials','c'],SimpleNamespace(returncode=1,stderr='PERMISSION_DENIED: container.clusters.get denied'))
    self.assertEqual(type(err).__name__,'AuthorizationCommandError')
    self.assertNotIn('KUBECONFIG=',str(err))
 def test_env_wrapper_does_not_interpret_shell_or_embedded_command(self):
  from evaluation.gke.cloud_identity import command_failure
  with tempfile.TemporaryDirectory() as d:
   c=Cluster(d)
   for args in (['env','-S','gcloud container clusters list'],['env','X=gcloud','sh','-c','echo PERMISSION_DENIED'],['env','X=y','curl','http://app'],['env','X=kubectl','python','app.py']):
    err=command_failure(c,args,SimpleNamespace(returncode=1,stderr='PERMISSION_DENIED'))
    self.assertIs(type(err),RuntimeError)
 def test_malformed_ssar_denied_and_evaluation_error_fail_closed(self):
  for status in ({'allowed':True,'denied':'true'},{'allowed':True,'denied':0},{'allowed':True,'denied':None},{'allowed':True,'evaluationError':False},[],None):
   with self.subTest(status=status),tempfile.TemporaryDirectory() as d:
    c=Cluster(d);old=c.kube
    def kube(*args,**kwargs):
     if args[:2]==('create','--raw') and 'selfsubjectaccessreviews' in args[2]:return SimpleNamespace(stdout=json.dumps({'status':status}),returncode=0)
     return old(*args,**kwargs)
    c.kube=kube
    with patch('evaluation.gke.provision.assert_project_lease'),patch('evaluation.gke.provision.verify_resource',return_value={'uid':'cluster-uid'}):
     with self.assertRaisesRegex(RuntimeError,'permission'):importlib.import_module('evaluation.gke.deployment_authorization').verify(c,1,clock=lambda:1000)
    self.assertEqual(c.objects,{})
 def test_history_authorization_failure_stops_once_with_preserved_attempt(self):
  from evaluation.gke.cloud_identity import AuthorizationCommandError
  from evaluation.tests.test_evidence_first_execution import EvidenceFirstExecutionTests
  for chained in (False,True):
   error=AuthorizationCommandError('permission denied')
   if chained:
    wrapper=RuntimeError('capture wrapper');wrapper.__cause__=error;error=wrapper
   with self.subTest(chained=chained):
    helper=EvidenceFirstExecutionTests();events,result=helper.run_case(profile='thesis-robustness-full-v1',history_outcomes=[error,'correct'])
    self.assertEqual(events.count('history'),1);self.assertEqual(events.count('measure'),1)
    self.assertEqual(result['records'][-1]['state'],'unattempted');self.assertEqual(len(result['history']['attempts']),1)
    self.assertTrue(result['history']['attempts'][0]['nonretryable_authorization'])
    self.assertIn('authorization',result['failure']['message'])
 def test_history_capture_preserves_authorization_marker_through_export(self):
  import time
  from evaluation.gke import thesis_history
  from evaluation.gke.cloud_identity import AuthorizationCommandError
  from evaluation.gke.models import write
  with tempfile.TemporaryDirectory() as d:
   ctx=SimpleNamespace(result=Path(d),run_id='r',deadline=time.time()+1000,config={'execution_profile':'thesis-robustness-full-v1','limits':{'cleanup_reserve_seconds':100}},manifest={'schedule':'thesis-completion-v2'},state={'terraform':{'foundation':{'evidence_bucket':'bucket'}}},write=write)
   with patch.object(thesis_history,'_capture',side_effect=AuthorizationCommandError('patch denied')),patch.object(thesis_history.export,'upload',return_value={'verified':True}) as upload:
    result=thesis_history.capture(ctx,SimpleNamespace(namespace='demo'),{'id':'history-a1'})
   upload.assert_called_once();self.assertTrue(result.get('nonretryable_authorization'))

 def test_preserved_history_authorization_report_stops_without_second_capture(self):
  from evaluation.tests.test_evidence_first_execution import EvidenceFirstExecutionTests
  returned={'answer':{'state':'insufficient','reason':'denied'},'nonretryable_authorization':True,'export':{'verified':True},'continuation_safe':True}
  events,result=EvidenceFirstExecutionTests().run_case(profile='thesis-robustness-full-v1',history_outcomes=[returned,'correct'])
  self.assertEqual(events.count('history'),1);self.assertEqual(events.count('measure'),1)
  self.assertTrue(result['history']['attempts'][0]['report']['nonretryable_authorization'])
  self.assertTrue(result['history']['attempts'][0]['report']['export']['verified'])
  self.assertIn('authorization',result['failure']['message'])

 def test_check_false_restart_denial_preserves_type_and_failed_intent(self):
  import time
  from evaluation.gke.context import LiveContext
  from evaluation.gke.cloud_identity import AuthorizationCommandError
  from evaluation.gke.diagnostics.restarts import restart_target
  from evaluation.gke.models import write
  for stage in ('get','patch'):
   with self.subTest(stage=stage),tempfile.TemporaryDirectory() as d:
    c=Cluster(d);c.manifest={};c.deadline=time.time()+1000;c.write=write;c.evidence_dir=Path(d)
    obj={'metadata':{'uid':'deployment-uid','resourceVersion':'1','labels':LABELS},'spec':{'template':{'metadata':{}}}}
    def executor(args,**kwargs):
     return SimpleNamespace(returncode=1 if args[1]==stage else 0,stdout=json.dumps(obj),stderr='Error from server (Forbidden): controller cannot '+stage)
    c.executor=executor;c.kube=lambda *args,**kwargs:LiveContext.command(c,['kubectl',*args],**kwargs)
    with self.assertRaises(AuthorizationCommandError):restart_target(c,'demo','checkout','run-a')
    self.assertEqual(len(list((Path(d)/'command-diagnostics').glob('*.stderr'))),1)
    if stage=='patch':
     intent=json.loads((Path(d)/'restart-run-a-checkoutservice.json').read_text())
     self.assertEqual(intent['state'],'failed');self.assertIn('Forbidden',intent['error'])

 def test_check_false_preserves_legacy_and_application_failures(self):
  import time
  from evaluation.gke.context import LiveContext
  for profile,args,error in (('legacy',['kubectl','get','pods'],'Error from server (Forbidden): denied'),('thesis-robustness-full-v1',['kubectl','logs','job/app'],'application HTTP 403 Forbidden'),('thesis-robustness-full-v1',['kubectl','wait','job/app'],'timed out waiting for condition')):
   with self.subTest(profile=profile,args=args),tempfile.TemporaryDirectory() as d:
    c=Cluster(d);c.manifest={};c.deadline=time.time()+1000;c.config['execution_profile']=profile
    result=SimpleNamespace(returncode=1,stdout='',stderr=error);c.executor=lambda *a,**kw:result
    self.assertIs(LiveContext.command(c,args,check=False),result)
