import copy,unittest
from evaluation.gke.render import transform
IMAGE='repo/app@sha256:'+'a'*64
class ResourceRequestTests(unittest.TestCase):
 def render(self,containers,*,name='frontend',mode='demo',init=None):
  pod={'kind':'Pod','metadata':{'name':name},'spec':{'containers':containers}}
  if init is not None:pod['spec']['initContainers']=init
  original=copy.deepcopy(pod);out=transform([pod],{'app:v1':IMAGE},run_id='fixture',mode=mode)[0];self.assertEqual(pod,original);return out
 def test_astronomy_telemetry_docs_uses_the_less_loaded_worker(self):
  out=self.render([{'name':'docs','image':'app:v1'}],name='telemetry-docs')
  self.assertEqual(out['spec']['nodeSelector']['evaluation.karthos.dev/slot'],'app-b')
  self.assertEqual(out['spec']['containers'][0]['resources']['requests']['cpu'],'100m')
 def test_missing_cpu_added_memory_limit_default_preserved(self):
  c={'name':'app','image':'app:v1','args':['unchanged'],'resources':{'limits':{'memory':'64Mi'}}};out=self.render([c])['spec']['containers'][0]
  self.assertEqual(out['resources']['requests'],{'cpu':'100m','memory':'64Mi'})
  self.assertEqual(out['resources']['limits'],c['resources']['limits']);self.assertEqual(out['args'],c['args'])
 def test_existing_requests_and_limits_are_unchanged(self):
  c={'name':'app','image':'app:v1','resources':{'requests':{'cpu':'123m','memory':'12Mi'},'limits':{'cpu':'400m','memory':'64Mi'}}}
  self.assertEqual(self.render([c])['spec']['containers'][0]['resources'],c['resources'])
 def test_cpu_limit_defaults_to_request_instead_of_exceeding_it(self):
  out=self.render([{'name':'app','image':'app:v1','resources':{'limits':{'cpu':'50m','memory':'32Mi'}}}])
  self.assertEqual(out['spec']['containers'][0]['resources']['requests'],{'cpu':'50m','memory':'32Mi'})
 def test_karthos_budget_adds_requests_without_creating_limits(self):
  out=self.render([{'name':'server','image':'app:v1'}],name='karthos-victoria-metrics-single-server',mode='karthos')
  self.assertEqual(out['spec']['containers'][0]['resources']['requests'],{'cpu':'300m','memory':'1Gi'})
  self.assertNotIn('limits',out['spec']['containers'][0]['resources'])
  self.assertEqual(out['metadata']['annotations']['evaluation.karthos.dev/resource-policy'],'requests-v1')
 def test_ordinary_init_and_restartable_sidecar_have_separate_budgets(self):
  out=self.render([{'name':'app','image':'app:v1'}],init=[{'name':'wait','image':'app:v1'},{'name':'sidecar','image':'app:v1','restartPolicy':'Always'}])
  self.assertEqual(out['spec']['initContainers'][0]['resources']['requests'],{'cpu':'10m','memory':'16Mi'})
  self.assertEqual(out['spec']['initContainers'][1]['resources']['requests'],{'cpu':'100m','memory':'128Mi'})
