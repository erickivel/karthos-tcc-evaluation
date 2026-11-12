import importlib.util,json,unittest
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[2]
def module():
 spec=importlib.util.spec_from_file_location('watchdog_router_fixture',ROOT/'infrastructure/watchdog/main.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
class RouterCleanupTests(unittest.TestCase):
 def test_owned_router_with_nat_uses_delete_without_network_policy_update(self):
  m=module();calls=[]
  class Session:
   def get(self,*a,**kw):return SimpleNamespace(status_code=200,raise_for_status=lambda:None,json=lambda:{'id':'123','description':json.dumps({'evaluation-owner':'research','evaluation-run':'run-1'}),'nats':[{'name':'owned-nat'}]})
   def patch(self,*a,**kw):raise AssertionError('router cleanup must not require network-policy update')
   def delete(self,url,**kw):calls.append(url);return SimpleNamespace(status_code=200,raise_for_status=lambda:None)
  r={'kind':'router','name':'projects/research-project/regions/us-central1/routers/eval','uid':'123','owner_id':'research','run_id':'run-1'}
  m.GCPCloud(Session()).delete(r);self.assertEqual(calls,['https://compute.googleapis.com/compute/v1/'+r['name']])
 def test_http_failure_keeps_status_and_bounded_provider_reason(self):
  m=module()
  class Denied(Exception):pass
  denied=Denied();denied.response=SimpleNamespace(status_code=403,json=lambda:{'error':{'code':403,'message':'Required compute.networks.updatePolicy permission'}})
  class Cloud:
   def verify_lease(self,lease):return True
   def list_owned_disks(self,record):return []
   def get(self,r):return {'uid':'123','owner_id':'research','run_id':'run-1'}
   def delete(self,r):raise denied
  record={'project_id':'research-project','owner_id':'research','run_id':'run-1','started_at':0,'deadline':1,'resources':[{'kind':'router','name':'projects/research-project/regions/us-central1/routers/eval','uid':'123'}]}
  result=m.enforce(record,Cloud(),now=2,dry_run=False);error=result['errors'][0]
  self.assertEqual(error.get('http_status'),403);self.assertIn('compute.networks.updatePolicy',error.get('message',''));self.assertIn(record['resources'][0]['name'],result['residual'])
