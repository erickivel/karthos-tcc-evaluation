import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from evaluation import timing_pilot as t
from evaluation.tests.test_generator_artifacts import bundle,rehash
from evaluation.generator_artifacts import validate_export

class TimingPilotTests(unittest.TestCase):
 def test_matrix_is_four_cells_sixteen_windows_with_alternating_order(self):
  plan=t.plan();self.assertEqual(len(plan),16)
  self.assertEqual([x['cell'] for x in plan[::4]],['r1-boutique-trace','r1-astronomy-trace','r1-boutique-mesh','r1-astronomy-mesh'])
  self.assertEqual([x['generator'] for x in plan[:4]],['k6','locust','k6','locust'])
  self.assertEqual([x['generator'] for x in plan[4:8]],['locust','k6','locust','k6'])
  self.assertEqual(sum(x['duration_seconds'] for x in plan),56*60)
 def test_probe_job_uses_real_tools_and_existing_recovery_wrapper(self):
  from evaluation.protocol import Cell
  with patch.dict(t.r.CONFIG,{'k6_image':'k6@sha256:a','artifact_recovery_seconds':1800}):
   spec=t.plan()[0];job=t.make_job(Cell('boutique','trace',1),spec,'run:probes:cell:k6')
  c=job['spec']['template']['spec']['containers'][0]
  self.assertIn('/scripts/timing_probe.js',c['args'][0]);self.assertIn('READY',c['args'][0]);self.assertIn('ACK',c['args'][0])
  self.assertEqual(next(x['value'] for x in c['env'] if x['name']=='RUN_ID'),'run:probes:cell:k6')
 def test_budget_reserves_export_before_starting_new_workload(self):
  with self.assertRaises(TimeoutError):t.reserve_budget(300,200)
  t.reserve_budget(300,1800)
 def test_probe_export_cannot_ack_a_matched_ledger_as_complete(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);bundle(p)
   with self.assertRaisesRegex(ValueError,'probe'):validate_export(p,'k6','timing-probe','run')
 def test_probe_materialization_keeps_ledger(self):
  from evaluation.generator_execution import _materialize
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);raw=p/'raw';raw.mkdir();bundle(raw)
   _materialize(raw,p,'k6','timing-probe')
   self.assertEqual(len((p/'ledger.ndjson').read_text().splitlines()),1)
 def test_run_identifier_cannot_escape_results(self):
  for run_id in ('../other','/tmp/x','a/b',''):
   with self.subTest(run_id=run_id),self.assertRaises(ValueError):t.safe_run_id(run_id)

class TimingSafetyTests(unittest.TestCase):
 def test_analysis_refuses_historical_root_without_mutating_it(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'run-report.json').write_text('{"protocol_version":4}')
   before=list(root.iterdir())
   with self.assertRaisesRegex(ValueError,'timing'):t.analyse(root)
   self.assertEqual(list(root.iterdir()),before)
 def test_restore_is_deferred_if_availability_worker_is_still_running(self):
  from unittest.mock import Mock
  capture=Mock();availability=Mock();availability.stop.return_value={'valid':False,'worker_stopped':False}
  result=t.stop_captures(availability,capture)
  capture.stop.assert_not_called()
  self.assertFalse(result['structured_capture']['valid'])
  self.assertTrue(result['structured_capture']['watchdog_retained'])

class TimingSummarySizeTests(unittest.TestCase):
 def test_summary_references_full_report_instead_of_duplicating_it(self):
  from hashlib import sha256
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);(root/'run-report.json').write_text(json.dumps({'protocol':t.PROTOCOL}))
   window=root/'measured/cell/windows/probe';window.mkdir(parents=True);(window/'manifest.json').write_text('{}')
   detailed={'state':'diagnostic','independent_valid':True,'schema_version':'fixture','events':{'large':'x'*1_000_000}}
   with patch('evaluation.timing_analysis.analyze_window',return_value=detailed):t.analyse(root)
   full=window/'timing-verification.json';summary=root/'timing-summary.json';data=json.loads(summary.read_text())
   self.assertLess(summary.stat().st_size,10_000)
   self.assertEqual(json.loads(full.read_text()),detailed)
   item=data['windows'][0];self.assertEqual(root/item['report_path'],full)
   self.assertEqual(item['report_sha256'],sha256(full.read_bytes()).hexdigest())
