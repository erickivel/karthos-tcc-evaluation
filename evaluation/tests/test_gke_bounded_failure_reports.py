import tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from evaluation.gke import recoverable_run as r
from evaluation.gke.models import write,sha
class BoundedFailureReportsTests(unittest.TestCase):
 def test_oversized_detailed_report_is_not_loaded_or_overwritten(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);raw=p/'gke-evaluation.json'
   with raw.open('wb') as f:f.truncate(9*1024*1024)
   before=sha(raw);report=r._retained_failure_report(p)
   self.assertFalse(report['evidence']['valid']);self.assertEqual(report['execution']['state'],'incomplete')
   r._persist_attempt_report(SimpleNamespace(write=write),p,report,preserve_detail=True)
   self.assertEqual(sha(raw),before);self.assertTrue((p/'recovery-verdict.json').exists())
 def test_live_report_keeps_existing_output_contract(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);report={'execution':{'state':'complete'},'evidence':{'valid':True}}
   r._persist_attempt_report(SimpleNamespace(write=write),p,report,preserve_detail=False)
   self.assertTrue((p/'gke-evaluation.json').exists());self.assertFalse((p/'recovery-verdict.json').exists())
if __name__=='__main__':unittest.main()
