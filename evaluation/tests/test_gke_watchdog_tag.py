import json,unittest
from types import SimpleNamespace
from evaluation.gke.provision import _watchdog_env

class WatchdogTagTests(unittest.TestCase):
 def test_generated_tag_fits_actual_cloud_run_service_name_limit(self):
  name='karthos-eval-f10ef77ad05c8418-watchdog';calls=[]
  def command(args,**kw):
   calls.append(args);tag=next(a.split('=',1)[1] for a in args if a.startswith('--tag='))
   return SimpleNamespace(stdout=json.dumps({'status':{'traffic':[{'tag':tag,'url':'https://candidate.example','revisionName':'watchdog-revision'}]}}))
  ctx=SimpleNamespace(config={'gcp':{'region':'us-central1','project_id':'research-project'}},state={'terraform':{'controller':{'watchdog_service_name':name},'foundation':{'evidence_bucket':'evidence'}},'inventory':{'object':'control/run/inventory/abc.json','generation':'123'}},command=command)
  self.assertEqual(_watchdog_env(ctx)['revision'],'watchdog-revision')
  tag=next(a.split('=',1)[1] for a in calls[0] if a.startswith('--tag='))
  self.assertLessEqual(len(name)+len(tag),46,'Cloud Run limits the combined tag and service name length')
  self.assertRegex(tag,r'^[a-z][a-z0-9-]*$')
