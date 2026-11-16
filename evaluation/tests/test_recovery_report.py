import json,tempfile,unittest
from pathlib import Path
from evaluation.gke.models import write
from evaluation.gke.recovery_report import build
class ReportTests(unittest.TestCase):
 def test_failed_window_stays_unattempted_and_original_unchanged(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);original=p/'original.json';out=p/'analysis';out.mkdir()
   write(original,{'records':[{'id':'official-completion-baseline-astronomy-mesh','attempt_id':'failed','state':'failed','attempts':[]}]})
   before=original.read_bytes();result=build(original,out)
   self.assertEqual(original.read_bytes(),before)
   self.assertEqual(result['windows'][0]['representative_accuracy'],'unattempted')
   self.assertTrue(all(c['verdict']=='insufficient' for c in result['consumer_claims']))
   with self.assertRaises(ValueError):build(original,out)
