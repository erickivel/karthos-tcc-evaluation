import unittest,tempfile
from pathlib import Path
from datetime import datetime
from types import SimpleNamespace
from evaluation.gke import provision,setup
from evaluation.tests.test_gke_provision import FakeContext
class VersionPinTests(unittest.TestCase):
 def nodes(self,version='v1.34.12-gke.1011000'):
  return {'items':[{'metadata':{'name':s,'uid':s,'labels':{'evaluation.karthos.dev/slot':s}},'status':{'conditions':[{'type':'Ready','status':'True'}],'nodeInfo':{'kubeletVersion':version}}} for s in ['observability','app-a','app-b']]}
 def test_window_version_check_records_server_and_workers(self):
  ctx=SimpleNamespace(config={'infrastructure':{'kubernetes_version':'1.34.12-gke.1011000'}},json=lambda *a:self.nodes() if a==('get','nodes') else {'serverVersion':{'gitVersion':'v1.34.12-gke.1011000'}})
  r=setup.verify_versions(ctx);self.assertTrue(r['valid']);self.assertEqual(len(r['nodes']),3)
 def test_worker_drift_is_rejected(self):
  ctx=SimpleNamespace(config={'infrastructure':{'kubernetes_version':'1.34.12-gke.1011000'}},json=lambda *a:self.nodes('v1.34.12-gke.1539000') if a==('get','nodes') else {'serverVersion':{'gitVersion':'v1.34.12-gke.1011000'}})
  with self.assertRaisesRegex(ValueError,'worker'):setup.verify_versions(ctx)
 def test_server_drift_is_rejected(self):
  ctx=SimpleNamespace(config={'infrastructure':{'kubernetes_version':'1.34.12-gke.1011000'}},json=lambda *a:self.nodes() if a==('get','nodes') else {'serverVersion':{'gitVersion':'v1.35.0'}})
  with self.assertRaisesRegex(ValueError,'server'):setup.verify_versions(ctx)
 def test_maintenance_window_does_not_extend_study_deadline(self):
  with tempfile.TemporaryDirectory() as tmp:
   ctx=FakeContext(Path(tmp));ctx.state['terraform']={'controller':{'network':'n','subnetwork':'s'},'foundation':{'node_service_account':'node@example.invalid'}}
   v=provision.cluster_variables(ctx)
   start=datetime.fromisoformat(v['maintenance_start_time'].replace('Z','+00:00')).timestamp();end=datetime.fromisoformat(v['maintenance_end_time'].replace('Z','+00:00')).timestamp()
   self.assertLessEqual(end,ctx.state['deadline']);self.assertLessEqual(end-start,43200);self.assertGreater(end,start)
