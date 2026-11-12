import json,tempfile,unittest
from pathlib import Path
try:
 from evaluation import native_boundaries as n
except ImportError:n=None

class NativeBoundaryTests(unittest.TestCase):
 def setUp(self):
  self.assertIsNotNone(n,'destination-aware native boundary verification is missing')
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.path=Path(self.temp.name);(self.path/'logs').mkdir()
  self.manifest=dict(start=10,end=20,run_id='r')
  self.rows=[dict(run_id='r',request_id='front',started_at=11,completed_at=12,status=200,expected_boundary='frontend',trace_id='1'*32,trace_context_evidence='response.request.headers',ledger_contract_version='native-v2-final-request'),dict(run_id='r',request_id='direct',started_at=13,completed_at=14,status=200,expected_boundary='agent',trace_id='2'*32,trace_context_evidence='response.request.headers',ledger_contract_version='native-v2-final-request')]
  self.obs=[dict(run_id='r',request_id='front',observed_at=11.9,boundary='frontend',status=200,traceparent='00-'+'1'*32+'-'+'3'*16+'-03'),dict(run_id='r',request_id='direct',observed_at=13.9,boundary='agent',status=200,traceparent='00-'+'2'*32+'-'+'4'*16+'-01')]
 def score(self):
  (self.path/'ledger.ndjson').write_text(''.join(json.dumps(r)+'\n' for r in self.rows))
  (self.path/'logs/combined__eval-observer.log').write_text(''.join(json.dumps(r)+'\n' for r in self.obs))
  return n.native_boundary_scores(self.path,self.manifest)
 def test_direct_agent_is_verified_at_agent_not_frontend(self):
  r=self.score();self.assertTrue(r['valid']);self.assertEqual(r['state'],'pass');self.assertEqual(r['boundaries']['frontend']['expected_requests'],1);self.assertEqual(r['boundaries']['agent']['expected_requests'],1)
 def test_missing_direct_observer_is_incomplete(self):
  self.obs.pop();r=self.score();self.assertFalse(r['valid']);self.assertEqual(r['state'],'incomplete')
 def test_trace_context_mismatch_is_not_successful_correlation(self):
  self.rows[0]['trace_id']='5'*32;self.assertFalse(self.score()['valid'])
 def test_unknown_destination_is_not_silently_excluded(self):
  self.rows[1]['expected_boundary']='unknown';self.assertFalse(self.score()['valid'])
 def test_missing_new_ledger_metadata_stays_unavailable(self):
  self.rows[0].pop('ledger_contract_version');self.assertFalse(self.score()['valid'])
 def test_numerical_status_disagreement_retains_valid_evidence(self):
  self.obs[1]['status']=503;r=self.score();self.assertTrue(r['valid']);self.assertEqual(r['state'],'fail')
 def test_completion_tail_is_retained_without_moving_window(self):
  self.rows[1].update(started_at=19.9,completed_at=20.2);self.obs[1]['observed_at']=20.1;r=self.score();self.assertTrue(r['valid']);self.assertEqual(r['scheduled_bounds'],[10,20]);self.assertGreater(r['observer_end'],20)

class ObserverTargetTests(unittest.TestCase):
 def test_agent_observer_only_for_declared_astronomy_native_paths(self):
  from evaluation.pilot import observer_targets
  from evaluation.protocol import Cell
  self.assertIn(('agent','agent',False),observer_targets(Cell('astronomy','trace',1),include_agent=True))
  self.assertNotIn(('agent','agent',False),observer_targets(Cell('astronomy','trace',1)))
  self.assertNotIn(('agent','agent',False),observer_targets(Cell('boutique','trace',1),include_agent=True))
