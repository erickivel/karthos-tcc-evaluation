import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from evaluation.gke.models import write
class StreamingWriteTests(unittest.TestCase):
 def test_json_uses_stream_encoder_and_preserves_bytes(self):
  obj={'unicode':'ação','observations':[{'x':1.5,'labels':{'b':True}}]*1000}
  expected=json.dumps(obj,indent=2,sort_keys=True)+'\n'
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'report.json'
   with patch('evaluation.gke.models.json.dumps',side_effect=AssertionError('whole JSON string allocated')):write(p,obj)
   self.assertEqual(p.read_text(),expected)
 def test_encoder_failure_preserves_previous_report_and_removes_partial(self):
  def broken(value,handle,**kwargs):handle.write('{partial');raise ValueError('encode failed')
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'report.json';p.write_text('previous\n')
   with patch('evaluation.gke.models.json.dump',side_effect=broken):
    with self.assertRaisesRegex(ValueError,'encode failed'):write(p,{'new':'report'})
   self.assertEqual(p.read_text(),'previous\n');self.assertEqual([x.name for x in Path(d).iterdir()],['report.json'])
 def test_text_bytes_unchanged(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'sub'/'text';write(p,'unchanged');self.assertEqual(p.read_text(),'unchanged')
if __name__=='__main__':unittest.main()
