import copy,importlib.util,unittest
from pathlib import Path
from types import SimpleNamespace
from evaluation.gke import models,repair,provision
ROOT=Path(__file__).resolve().parents[2]
class OfficialLimitsTests(unittest.TestCase):
 def test_new_limits_are_scoped_to_official_protocol(self):
  config=models.read(ROOT/'config/gke.completion-v2.example.yaml');config['limits'].update(gross_limit_usd=200,infrastructure_seconds=64800)
  models.validate_config(config,schedule=models.COMPLETION_V2)
  with self.assertRaises(ValueError):models.validate_config(config,schedule=models.COMPLETION)
  config['limits']['infrastructure_seconds']=64801
  with self.assertRaises(ValueError):models.validate_config(config,schedule=models.COMPLETION_V2)
 def test_combined_budget_and_context_accept_approved_bounds_only(self):
  config={'limits':{'gross_limit_usd':200,'infrastructure_seconds':64800},'readiness':{'study_budget':{'gross_limit_usd':200,'combined_estimate_usd':160,'prior_runtime_allowance_usd':72,'shared_allowances_cumulative':True,'basis':'reviewed'}}}
  manifest={'schedule':models.COMPLETION_V2,'config':config,'study_budget':config['readiness']['study_budget'],'cost_preview':{'gross_estimate':80}}
  repair.validate_budget(manifest)
  ctx=SimpleNamespace(config=config,run_id='test',state={'run_id':'test','owner_id':provision.OWNER,'started_at':1,'deadline':64801})
  provision.require_context(ctx)
  ctx.state['deadline']+=1
  with self.assertRaises(ValueError):provision.require_context(ctx)
 def test_inventory_binds_the_official_deadline_cap(self):
  ctx=SimpleNamespace(config={'gcp':{'project_id':'research-project'},'limits':{'infrastructure_seconds':64800}},manifest={'schedule':models.COMPLETION_V2},run_id='test',deadline=64800,state={'started_at':0})
  self.assertEqual(provision._inventory_record(ctx,[],[])['infrastructure_seconds'],64800)
  ctx.manifest['schedule']=models.COMPLETION
  self.assertNotIn('infrastructure_seconds',provision._inventory_record(ctx,[],[]))
 def test_watchdog_long_deadline_requires_explicit_reviewed_limit(self):
  spec=importlib.util.spec_from_file_location('review_watchdog',ROOT/'infrastructure/watchdog/main.py');watchdog=importlib.util.module_from_spec(spec);spec.loader.exec_module(watchdog)
  base={'project_id':'research-project','owner_id':'research','run_id':'run-1','started_at':0,'deadline':64800,'resources':[]}
  with self.assertRaisesRegex(ValueError,'deadline'):watchdog.enforce(base,None,now=1)
  base['infrastructure_seconds']=64800
  with self.assertRaisesRegex(ValueError,'nonempty'):watchdog.enforce(base,None,now=1)
  base['infrastructure_seconds']=64801
  with self.assertRaises(ValueError):watchdog.enforce(base,None,now=1)
