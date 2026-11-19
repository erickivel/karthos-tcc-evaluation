import copy,json,unittest
from pathlib import Path
from evaluation.gke.models import build_matrix,COMPLETION_V2,validate_schedule
class TargetedProfileTests(unittest.TestCase):
 def protocol(self):return json.loads(Path('protocols/thesis-completion-v2/protocol.yaml').read_text())
 def test_exact_profile_and_original_preserved(self):
  p=self.protocol();original=build_matrix(p,schedule=COMPLETION_V2)
  p.update(execution_profile='astronomy-recovery-v1',combined_gross_limit_usd=240);rows=build_matrix(p,schedule=COMPLETION_V2)
  self.assertEqual([(r['cell'],r['phase']) for r in rows],[('astronomy-trace','smoke'),('astronomy-trace','baseline'),('astronomy-mesh','smoke'),('astronomy-mesh','baseline')])
  self.assertEqual(sum(r['duration_seconds'] for r in rows),720)
  self.assertFalse(any(r.get('history_observation') for r in rows));self.assertEqual(len(original),14)
  validate_schedule({'schedule':COMPLETION_V2,'protocol_definition':p,'matrix':rows})
  with self.assertRaises(ValueError):validate_schedule({'schedule':COMPLETION_V2,'protocol_definition':p,'matrix':rows[:-1]})
 def test_unknown_profile_rejected(self):
  p=self.protocol();p['execution_profile']='arbitrary-subset'
  with self.assertRaises(ValueError):build_matrix(p,schedule=COMPLETION_V2)

 def test_targeted_approved_240_ceiling_and_original_200(self):
  from evaluation.gke.models import validate_config
  c=json.loads(Path('config/gke.completion-v2.example.yaml').read_text())
  c['execution_profile']='astronomy-recovery-v1';c['limits']['gross_limit_usd']=240
  validate_config(c,schedule=COMPLETION_V2)
  p=self.protocol();p.update(execution_profile='astronomy-recovery-v1',combined_gross_limit_usd=240)
  self.assertEqual(len(build_matrix(p,schedule=COMPLETION_V2)),4)
  c.pop('execution_profile')
  with self.assertRaises(ValueError):validate_config(c,schedule=COMPLETION_V2)

 def test_only_failed_istio_windows_in_repair_profile(self):
  p=self.protocol();p.update(execution_profile='astronomy-istio-repair-v1',combined_gross_limit_usd=240)
  rows=build_matrix(p,schedule=COMPLETION_V2)
  self.assertEqual([(r['cell'],r['phase']) for r in rows],[('astronomy-mesh','smoke'),('astronomy-mesh','baseline')])
  self.assertEqual(sum(r['duration_seconds'] for r in rows),360)
  self.assertEqual(rows[0]['predecessor_window'],'targeted-smoke-astronomy-mesh')

 def test_approved_270_is_scoped_to_evidence_first_istio_repair(self):
  from evaluation.gke.models import validate_config
  c=json.loads(Path('config/gke.completion-v2.example.yaml').read_text())
  c.update(execution_profile='astronomy-istio-repair-v1',evidence_pipeline='sealed-before-analysis-v1')
  c['limits']['gross_limit_usd']=270
  validate_config(c,schedule=COMPLETION_V2)
  p=self.protocol();p.update(execution_profile='astronomy-istio-repair-v1',combined_gross_limit_usd=270)
  self.assertEqual(len(build_matrix(p,schedule=COMPLETION_V2)),2)
  c.pop('evidence_pipeline')
  with self.assertRaises(ValueError):validate_config(c,schedule=COMPLETION_V2)
  p['execution_profile']='astronomy-recovery-v1'
  with self.assertRaises(ValueError):build_matrix(p,schedule=COMPLETION_V2)

 def test_approved_270_passes_launch_budget_binding(self):
  from evaluation.gke.repair import validate_budget
  c=json.loads(Path('config/gke.completion-v2.example.yaml').read_text())
  c.update(execution_profile='astronomy-istio-repair-v1',evidence_pipeline='sealed-before-analysis-v1');c['limits']['gross_limit_usd']=270
  b=dict(prior_runtime_allowance_usd=190.18,combined_estimate_usd=263.01,gross_limit_usd=270,shared_allowances_cumulative=True,basis='reviewed conservative bound')
  c['readiness']['study_budget']=b
  p=self.protocol();p.update(execution_profile='astronomy-istio-repair-v1',combined_gross_limit_usd=270)
  m=dict(config=c,protocol_definition=p,schedule=COMPLETION_V2,study_budget=b,cost_preview={'gross_estimate':72.83})
  validate_budget(m)
  c.pop('evidence_pipeline')
  with self.assertRaises(ValueError):validate_budget(m)

 def test_two_failed_istio_baselines_only(self):
  p=self.protocol();p.update(execution_profile='failed-istio-baselines-v1',combined_gross_limit_usd=270)
  rows=build_matrix(p,schedule=COMPLETION_V2)
  self.assertEqual([(x['cell'],x['phase'],x['duration_seconds']) for x in rows],[('astronomy-mesh','baseline',300),('boutique-mesh','baseline',300)])
  self.assertTrue(all(not x['history_observation'] for x in rows))
  self.assertTrue(all(x['predecessor_window'].startswith('official-') for x in rows))
  validate_schedule({'schedule':COMPLETION_V2,'protocol_definition':p,'matrix':rows})
  from evaluation.gke.recoverable_run import history_required
  self.assertFalse(history_required(COMPLETION_V2,rows))
  self.assertTrue(history_required(COMPLETION_V2,build_matrix(self.protocol(),schedule=COMPLETION_V2)))

 def test_astronomy_baseline_only_preserves_other_protocols(self):
  from evaluation.gke.models import validate_config
  p=self.protocol();p.update(execution_profile='astronomy-istio-baseline-v1',combined_gross_limit_usd=270)
  rows=build_matrix(p,schedule=COMPLETION_V2)
  self.assertEqual([(r['cell'],r['phase'],r['duration_seconds'],r['rate']) for r in rows],[('astronomy-mesh','baseline',300,2)])
  self.assertFalse(rows[0]['history_observation'])
  self.assertEqual(rows[0]['predecessor_window'],'istio-baseline-repair-astronomy-mesh')
  validate_schedule({'schedule':COMPLETION_V2,'protocol_definition':p,'matrix':rows})
  self.assertEqual(len(build_matrix(self.protocol(),schedule=COMPLETION_V2)),14)
  c=json.loads(Path('config/gke.completion-v2.example.yaml').read_text())
  c.update(execution_profile='astronomy-istio-baseline-v1',evidence_pipeline='sealed-before-analysis-v1');c['limits']['gross_limit_usd']=270
  validate_config(c,schedule=COMPLETION_V2)
