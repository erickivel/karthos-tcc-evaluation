import hashlib,io,json,unittest
from evaluation.gke.analysis_reader import read_analysis_report
class ReaderTests(unittest.TestCase):
 def test_projection_validates_all_bytes_and_keeps_comparison_inputs(self):
  doc={'numerical_agreement':{'checks':[{'observed':{'value':2},'matching_references':[{'value':2}],'state':'pass'}]},'traffic':{'report':{'availability':{'observations':[1,2]},'keep':3}}}
  raw=json.dumps(doc).encode();result,size=read_analysis_report(io.BytesIO(raw),hashlib.sha256(raw).hexdigest())
  self.assertEqual(size,len(raw));self.assertEqual(result['numerical_agreement']['checks'][0],{'observed':{'value':2},'state':'pass'})
  self.assertEqual(result['traffic']['report']['keep'],3)
  with self.assertRaises(ValueError):read_analysis_report(io.BytesIO(raw),'0'*64)
