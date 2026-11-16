import tempfile,unittest
from pathlib import Path
from evaluation.gke.boundary_reconstruction import reconstruct
class BoundaryTests(unittest.TestCase):
 def test_checksum_failure_cannot_create_analysis(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);a=p/'archive';a.write_bytes(b'invalid')
   with self.assertRaisesRegex(ValueError,'checksum'):reconstruct(a,'0'*64,p/'out')
   self.assertFalse((p/'out').exists())
 def test_post_window_increment_does_not_move_into_fixed_window(self):
  from evaluation.gke.boundary_reconstruction import counter_boundaries
  points=[{'timestamp_ns':int(t*1e9),'value':v} for t,v in [(0,2),(15,5),(30,8),(45,11),(60,14)]]
  baseline,last,tail=counter_boundaries(points,1,53)
  self.assertEqual(last['value']-baseline['value'],9)
  self.assertEqual(tail['value']-baseline['value'],12)
  with self.assertRaises(ValueError):counter_boundaries(points[:-1],1,53)
 def test_reset_is_not_misdiagnosed_as_delayed_emission(self):
  from evaluation.gke.boundary_reconstruction import counter_boundaries
  with self.assertRaises(ValueError):counter_boundaries([{'timestamp_ns':0,'value':2},{'timestamp_ns':2_000_000_000,'value':0},{'timestamp_ns':4_000_000_000,'value':3}],1,3)
