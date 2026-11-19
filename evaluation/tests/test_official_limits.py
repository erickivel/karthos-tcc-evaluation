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
 def test_official_launch_threshold_is_ninety_only(self):
  config=models.read(ROOT/'config/gke.completion-v2.example.yaml')
  self.assertEqual(config['limits']['launch_ceiling_usd'],90)
  models.validate_config(config,schedule=models.COMPLETION_V2)
  for invalid in (80,91):
   candidate=copy.deepcopy(config);candidate['limits']['launch_ceiling_usd']=invalid
   with self.assertRaises(ValueError):models.validate_config(candidate,schedule=models.COMPLETION_V2)
  legacy=models.read(ROOT/'config/gke.example.yaml')
  self.assertEqual(legacy['limits']['launch_ceiling_usd'],80)
  models.validate_config(legacy)
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
 def test_full_profile_context_supports_twenty_hours_only_with_marker(self):
  ctx=SimpleNamespace(config={'execution_profile':'thesis-robustness-full-v1','limits':{'infrastructure_seconds':72000}},run_id='test',state={'run_id':'test','owner_id':provision.OWNER,'started_at':1,'deadline':72001})
  provision.require_context(ctx)
  ctx.config.pop('execution_profile')
  with self.assertRaises(ValueError):provision.require_context(ctx)
  ctx.config['execution_profile']='thesis-robustness-full-v1'
  for invalid in (86401,True,None,'72000',72000.0):
   ctx.config['limits']['infrastructure_seconds']=invalid
   with self.subTest(limit=invalid),self.assertRaises(ValueError):provision.require_context(ctx)
  ctx.config['limits']['infrastructure_seconds']=86400
  ctx.state['deadline']=86402
  with self.assertRaises(ValueError):provision.require_context(ctx)

 def test_full_inventory_marker_binds_watchdog_limit(self):
  spec=importlib.util.spec_from_file_location('review_watchdog_full',ROOT/'infrastructure/watchdog/main.py');watchdog=importlib.util.module_from_spec(spec);spec.loader.exec_module(watchdog)
  ctx=SimpleNamespace(config={'execution_profile':'thesis-robustness-full-v1','gcp':{'project_id':'research-project'},'limits':{'infrastructure_seconds':72000}},manifest={'schedule':models.COMPLETION_V2},run_id='test',deadline=72000,state={'started_at':0})
  record=provision._inventory_record(ctx,[],[])
  self.assertEqual(record.get('execution_profile'),'thesis-robustness-full-v1')
  self.assertEqual(watchdog.deadline_limit(record),72000)
  for limit in (43200,64800,72000,86400):
   record['infrastructure_seconds']=limit
   self.assertEqual(watchdog.deadline_limit(record),limit)
  for invalid in (86401,True,None,'72000',72000.0,0,-1):
   record['infrastructure_seconds']=invalid
   with self.subTest(limit=invalid),self.assertRaises(ValueError):watchdog.deadline_limit(record)
  record['infrastructure_seconds']=72000
  for profile in (None,'boutique-complement-v1','unknown'):
   record['execution_profile']=profile
   with self.subTest(profile=profile),self.assertRaises(ValueError):watchdog.deadline_limit(record)
  record.pop('execution_profile')
  with self.assertRaises(ValueError):watchdog.deadline_limit(record)
 def test_full_bootstrap_preserves_twenty_hour_deadline_and_existing_state(self):
  import tempfile,json,os
  from unittest.mock import patch
  from evaluation.gke.context import LiveContext
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);bootstrap=root/'bootstrap.json'
   manifest={'id':'test','config':{'execution_profile':'thesis-robustness-full-v1','gcp':{'project_id':'research-project'},'limits':{'infrastructure_seconds':72000}},'artifact_checks':{}}
   data={'run_id':'test','owner_id':provision.OWNER,'project_id':'research-project','started_at':10,'deadline':72010}
   bootstrap.write_text(json.dumps(data))
   with patch.dict(os.environ,{'GKE_EVAL_BOOTSTRAP_CONTEXT':str(bootstrap),'GKE_EVAL_DATA_ROOT':d}),patch('evaluation.gke.context.check_manifest',return_value=manifest),patch('evaluation.gke.context.classify_readiness',return_value=[]):
    ctx=LiveContext(root,root/'manifest.json')
    self.assertEqual(ctx.deadline,72010)
    data['deadline']=86410;bootstrap.write_text(json.dumps(data))
    self.assertEqual(LiveContext(root,root/'manifest.json').deadline,72010)
    (ctx.work/'execution.json').unlink()
    with self.assertRaises(ValueError):LiveContext(root,root/'manifest.json')
    data['deadline']=72010;bootstrap.write_text(json.dumps(data));manifest['config'].pop('execution_profile')
    with self.assertRaises(ValueError):LiveContext(root,root/'manifest.json')
