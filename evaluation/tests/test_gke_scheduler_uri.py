import json,tempfile,unittest
from pathlib import Path
from evaluation.gke import provision as p
from evaluation.tests.test_gke_provision import CloudContext

class SchedulerURITests(unittest.TestCase):
 def test_only_empty_path_and_single_root_slash_are_equivalent(self):
  self.assertTrue(p._same_watchdog_endpoint('https://watchdog.example/','https://watchdog.example'))
  self.assertTrue(p._same_watchdog_endpoint('https://watchdog.example','https://watchdog.example/'))
  for actual in ('http://watchdog.example/','https://other.example/','https://watchdog.example/path','https://watchdog.example//','https://watchdog.example/?x=1','https://watchdog.example/#x','https://user@watchdog.example/','https://watchdog.example:443/',None):
   self.assertFalse(p._same_watchdog_endpoint(actual,'https://watchdog.example'),actual)
 def test_actual_scheduler_canonical_target_passes_provisioning(self):
  with tempfile.TemporaryDirectory() as d:
   ctx=CloudContext(Path(d));original=ctx.command
   def command(args,**kw):
    result=original(args,**kw)
    if list(args)[:4]==['gcloud','scheduler','jobs','describe']:
     data=json.loads(result.stdout);data['httpTarget']['uri']='https://watchdog.example/';data['httpTarget']['oidcToken']['audience']='https://watchdog.example';result.stdout=json.dumps(data)
    return result
   ctx.command=command;self.assertEqual(p.provision(ctx)['phase'],'provisioned')
 def test_wrong_audience_stops_before_compute_creation(self):
  with tempfile.TemporaryDirectory() as d:
   ctx=CloudContext(Path(d));original=ctx.command
   def command(args,**kw):
    result=original(args,**kw)
    if list(args)[:4]==['gcloud','scheduler','jobs','describe']:
     data=json.loads(result.stdout);data['httpTarget']['oidcToken']['audience']='https://other.example';result.stdout=json.dumps(data)
    return result
   ctx.command=command
   with self.assertRaisesRegex(ValueError,'Scheduler readiness'):p.provision(ctx)
   self.assertFalse(ctx.exists['instance'])
