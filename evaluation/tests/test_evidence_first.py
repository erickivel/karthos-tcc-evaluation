import tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from evaluation.gke import evidence_first

class EvidenceFirstTests(unittest.TestCase):
 def test_scoring_never_runs_before_verified_evidence(self):
  called=[]
  with self.assertRaises(ValueError):
   evidence_first.after_export({'verified':False},lambda:called.append('score'))
  self.assertEqual(called,[])
 def test_score_failure_keeps_export_and_does_not_request_traffic_retry(self):
  def fail():raise RuntimeError('worker exit -9')
  receipt={'verified':True,'members':{'raw':{'sha256':'abc'}}}
  report=evidence_first.after_export(receipt,fail)
  self.assertEqual(report['analysis']['state'],'failed')
  self.assertEqual(report['raw_evidence_export'],receipt)
  self.assertFalse(report['retry_traffic'])
 def test_success_retains_raw_binding(self):
  receipt={'verified':True}
  report=evidence_first.after_export(receipt,lambda:{'execution':{'state':'complete'},'evidence':{'valid':True}})
  self.assertEqual(report['analysis']['state'],'complete')
  self.assertEqual(report['raw_evidence_export'],receipt)
 def test_worker_limit_and_join(self):
  with tempfile.TemporaryDirectory() as d:
   result=evidence_first.run_process(['python-does-not-exist'],Path(d),timeout=2)
   self.assertEqual(result['state'],'failed')
   self.assertTrue(result['workers_stopped'])

class AnalysisSummaryTests(unittest.TestCase):
 def test_unexpected_outcomes_survive_bounded_summary(self):
  from evaluation.gke.models import write
  from evaluation.gke.report_summary import save_summary
  with tempfile.TemporaryDirectory() as d:
   path=Path(d);report={'traffic':{'report':{'generator_contract':{'retained_generator':{'unexpected_outcomes':3}}}}}
   write(path/'gke-evaluation.json',report)
   summary=save_summary(path,report)
   self.assertEqual(summary['traffic']['report']['generator_contract']['retained_generator']['unexpected_outcomes'],3)

class InputBindingTests(unittest.TestCase):
 def test_verified_upload_does_not_authorize_changed_local_inputs(self):
  from evaluation.gke.export import seal
  from evaluation.gke.models import sha
  from evaluation.gke.evidence_first import verify_inputs
  with tempfile.TemporaryDirectory() as d:
   path=Path(d);(path/'raw.json').write_text('{}');seal(path)
   receipt={'verified':True,'original_window_seal_sha256':sha(path/'GKE-SHA256SUMS')}
   verify_inputs(path,receipt)
   (path/'raw.json').write_text('{"changed":true}')
   with self.assertRaisesRegex(ValueError,'checksum'):verify_inputs(path,receipt)

class AnalysisSafetyTests(unittest.TestCase):
 def test_unstopped_worker_is_not_an_ordinary_analysis_failure(self):
  def unsafe():raise evidence_first.AnalysisSafetyError('worker still running')
  with self.assertRaises(evidence_first.AnalysisSafetyError):
   evidence_first.after_export({'verified':True},unsafe)

class WorkerProcessTests(unittest.TestCase):
 def test_nonzero_exit_is_recorded_without_claiming_oom(self):
  import sys
  with tempfile.TemporaryDirectory() as d:
   result=evidence_first.run_process([sys.executable,'-c','import os; os._exit(137)'],Path(d),timeout=3)
   self.assertEqual(result['returncode'],137)
   self.assertEqual(result['state'],'failed')
   self.assertTrue(result['workers_stopped'])
   self.assertNotIn('OOM',result['reason'])
 def test_timeout_joins_worker_before_return(self):
  import sys
  with tempfile.TemporaryDirectory() as d:
   result=evidence_first.run_process([sys.executable,'-c','import time; time.sleep(10)'],Path(d),timeout=.1)
   self.assertEqual(result['state'],'failed')
   self.assertEqual(result['reason'],'analysis deadline exceeded')
   self.assertTrue(result['workers_stopped'])
