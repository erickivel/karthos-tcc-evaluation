import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from evaluation.gke import analysis
class StreamingAnalysisTests(unittest.TestCase):
 def test_large_report_uses_verified_projection_without_full_decode(self):
  raw=json.dumps({'numerical_agreement':{'state':'insufficient','checks':[{'state':'pass','observed':{'value':3},'matching_references':[{'value':3}]}]}}).encode();digest=hashlib.sha256(raw).hexdigest()
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'report.json';p.write_bytes(raw)
   with patch.object(analysis,'ANALYSIS_INLINE_BYTES',0),patch.object(Path,'read_text',side_effect=AssertionError('whole-file read')):
    result=analysis._read_window_report(p,digest)
   self.assertEqual(result['numerical_agreement']['state'],'insufficient');self.assertEqual(result['numerical_agreement']['checks'][0]['state'],'pass')
   self.assertEqual(p.read_bytes(),raw)
 def test_small_report_rejects_changed_bytes(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'report.json';p.write_text('{"value":2}')
   with self.assertRaisesRegex(ValueError,'checksum'):analysis._read_window_report(p,'0'*64)
if __name__=='__main__':unittest.main()
