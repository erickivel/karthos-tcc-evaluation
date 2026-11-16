import importlib,unittest
class SetupTests(unittest.TestCase):
 def module(self,n):
  try:return importlib.import_module('evaluation.gke.'+n)
  except ImportError:self.fail('GKE '+n+' missing')
 def test_frontend_and_checkout_have_opposite_node_slots(self):
  m=self.module('render');image='repo/app:v1';lock={image:'repo/app@sha256:'+'a'*64}
  def deployment(n):return {'kind':'Deployment','metadata':{'name':n,'namespace':'karthos-eval-boutique'},'spec':{'template':{'spec':{'containers':[{'name':'app','image':image}]}}}}
  a,b=m.transform([deployment('frontend'),deployment('checkoutservice')],lock,run_id='fixture',mode='demo')
  self.assertEqual(a['spec']['template']['spec']['nodeSelector']['evaluation.karthos.dev/slot'],'app-a');self.assertEqual(b['spec']['template']['spec']['nodeSelector']['evaluation.karthos.dev/slot'],'app-b')
 def test_astronomy_payment_uses_node_b_without_moving_frontend(self):
  m=self.module('render');image='repo/app:v1';lock={image:'repo/app@sha256:'+'a'*64}
  obj={'kind':'Deployment','metadata':{'name':'payment'},'spec':{'template':{'spec':{'containers':[{'name':'payment','image':image}]}}}}
  rendered=m.transform([obj],lock,run_id='fixture',mode='demo')[0]
  self.assertEqual(rendered['spec']['template']['spec']['nodeSelector']['evaluation.karthos.dev/slot'],'app-b')

 def test_currency_rebalance_preserves_requests(self):
  m=self.module('render');image='repo/app:v1';requests={'cpu':'100m','memory':'20Mi'}
  obj={'kind':'Deployment','metadata':{'name':'currency'},'spec':{'template':{'spec':{'containers':[{'name':'currency','image':image,'resources':{'requests':requests}}]}}}}
  out=m.transform([obj],{image:'repo/app@sha256:'+'a'*64},run_id='fixture',mode='demo')[0]['spec']['template']['spec']
  self.assertEqual(out['nodeSelector']['evaluation.karthos.dev/slot'],'app-a')
  self.assertEqual(out['containers'][0]['resources']['requests'],requests)

 def test_unpinned_image_fails_instead_of_deploying_tag(self):
  m=self.module('render')
  with self.assertRaisesRegex(ValueError,'image'):m.transform([{'kind':'Pod','metadata':{'name':'bad'},'spec':{'containers':[{'image':'unreviewed:latest'}]}}],{},run_id='fixture',mode='demo')
 def test_agent_daemonset_is_not_pinned_to_observability_node(self):
  m=self.module('render');i='repo/app:v1';obj={'kind':'DaemonSet','metadata':{'name':'karthos-agent','namespace':'karthos-system'},'spec':{'template':{'spec':{'containers':[{'name':'agent','image':i}]}}}}
  out=m.transform([obj],{i:'repo/app@sha256:'+'a'*64},run_id='fixture',mode='karthos')[0]
  self.assertNotIn('evaluation.karthos.dev/slot',out['spec']['template']['spec'].get('nodeSelector',{}))
 def test_node_inventory_requires_distinct_api_uids_and_slots(self):
  m=self.module('setup')
  with self.assertRaises(ValueError):m.validate_nodes({'items':[]})

 def test_pinned_tag_plus_digest_matches_reviewed_digest(self):
  m=self.module('render');digest='a'*64
  out=m.transform([{'kind':'Pod','metadata':{'name':'frontend'},'spec':{'containers':[{'name':'app','image':'grafana/k6:1.8.2@sha256:'+digest}]}}],{'grafana/k6:1.8.2':'docker.io/grafana/k6@sha256:'+digest},run_id='fixture',mode='demo')
  self.assertIn('@sha256:',out[0]['spec']['containers'][0]['image'])
 def test_generator_placement_and_owned_persistent_claims(self):
  m=self.module('render');digest='a'*64
  objects=[{'kind':'Job','metadata':{'name':'generator'},'spec':{'template':{'spec':{'containers':[{'image':'app:v1'}]}}}}, {'kind':'StatefulSet','metadata':{'name':'db'},'spec':{'volumeClaimTemplates':[{'metadata':{'name':'data'},'spec':{}}]}}]
  out=m.transform(objects,{'app:v1':'repo/app@sha256:'+digest},run_id='fixture',mode='demo')
  self.assertEqual(out[0]['spec']['template']['spec']['nodeSelector']['evaluation.karthos.dev/slot'],'observability')
  claim=out[1]['spec']['volumeClaimTemplates'][0];self.assertTrue(claim['spec']['storageClassName'].startswith('eval-'));self.assertEqual(claim['metadata']['labels']['evaluation.karthos.dev/run-id'],'fixture')

 def test_lock_value_cannot_silently_replace_a_tag_with_another_tag(self):
  m=self.module('render')
  with self.assertRaisesRegex(ValueError,'mutable'):m.transform([],{'app:v1':'app:latest'},run_id='fixture',mode='demo')

 def test_controller_space_covers_remaining_raw_and_sealed_archives(self):
  from types import SimpleNamespace
  m=self.module('setup');ctx=SimpleNamespace(config={'limits':{'max_evidence_bytes':100*1024**3}},state={})
  with self.assertRaisesRegex(ValueError,'headroom'):m.controller_headroom(ctx,50*1024**3)
  self.assertEqual(m.controller_headroom(ctx,250*1024**3)['required_bytes'],220*1024**3)
  ctx.state['cumulative_raw_evidence_bytes']=60*1024**3
  self.assertEqual(m.controller_headroom(ctx,110*1024**3)['required_bytes'],100*1024**3)

 def test_product_mirror_rewrites_original_tag_and_digest_without_weakening_identity(self):
  from types import SimpleNamespace
  m=self.module('setup');digest='a'*64
  tag='localhost:5001/karthos-backend:validated';original='localhost:5001/karthos-backend@sha256:'+digest
  mirror='us-central1-docker.pkg.dev/project/eval/karthos-backend@sha256:'+digest
  ctx=SimpleNamespace(manifest={'image_lock':{tag:original}},config={'infrastructure':{'product_image_mirrors':{'karthos-backend':mirror}}})
  lock=m.deployment_lock(ctx)
  self.assertEqual(lock[tag],mirror);self.assertEqual(lock[original],mirror)
  self.assertEqual(lock['karthos-backend:latest'],mirror)
  for image in (tag,original,'karthos-backend:latest'):
   obj={'kind':'Pod','metadata':{'name':'backend'},'spec':{'containers':[{'name':'backend','image':image}]}}
   self.assertEqual(self.module('render').transform([obj],lock,run_id='fixture',mode='karthos')[0]['spec']['containers'][0]['image'],mirror)
  ctx.config['infrastructure']['product_image_mirrors']['karthos-backend']=mirror[:-64]+'b'*64
  with self.assertRaisesRegex(ValueError,'preserve validated image digest'):m.deployment_lock(ctx)

 def test_mesh_creates_owned_namespace_before_namespaced_manifest(self):
  import json,tempfile
  from pathlib import Path
  from types import SimpleNamespace
  m=self.module('setup')
  namespace=None
  service_account={'apiVersion':'v1','kind':'ServiceAccount','metadata':{'name':'istio-reader-service-account','namespace':'istio-system'}}
  def kube(*args,**kwargs):
   nonlocal namespace
   if args[:3]==('get','namespace','istio-system'):
    return SimpleNamespace(returncode=0 if namespace or '--ignore-not-found' in args else 1,
                           stdout=json.dumps(namespace) if namespace else '')
   if args[:3]==('create','-f','-'):
    namespace=json.loads(kwargs['input_text'])
    return SimpleNamespace(returncode=0)
   if args[:3]==('apply','-f','-'):
    self.assertIsNotNone(namespace,'pinned Istio renderer does not emit a Namespace')
    self.assertEqual(json.loads(kwargs['input_text'])['items'][0]['kind'],'ServiceAccount')
   return SimpleNamespace(returncode=0)
  def command(args):
   if args[1]=='manifest':
    for setting in ('proxy.image','proxy_init.image'):
     self.assertIn('values.global.'+setting+'=docker.io/istio/proxyv2@sha256:'+'a'*64,args)
   return SimpleNamespace(stdout='1.28.2\n' if args[1]=='version' else json.dumps(service_account))
  with tempfile.TemporaryDirectory() as directory:
   ctx=SimpleNamespace(manifest={'image_lock':{}},config={'infrastructure':{'extra_image_lock':{'docker.io/istio/proxyv2:1.28.2':'docker.io/istio/proxyv2@sha256:'+'a'*64}}},
                       run_id='fixture',result=Path(directory),command=command,kube=kube)
   m.prepare_source(ctx,'mesh')
   self.assertEqual(namespace['metadata'],{'name':'istio-system','labels':{
       'evaluation.karthos.dev/owner':'gke-qualification-v1','evaluation.karthos.dev/run-id':'fixture'}})
   namespace['metadata']['labels']['evaluation.karthos.dev/run-id']='foreign'
   with self.assertRaisesRegex(ValueError,'unowned Istio namespace'):m.prepare_source(ctx,'mesh')
