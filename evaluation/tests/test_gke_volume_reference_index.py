import copy,unittest
from evaluation.gke import evaluate as e
import test_accuracy_evidence_repairs as fixtures
class VolumeReferenceIndexTests(unittest.TestCase):
 def test_index_keeps_values_duplicates_conflicts_and_nanosecond_precision(self):
  obs,inputs,refs,snapshots=fixtures.AccuracyEvidenceRepairs().volume()
  for selected in (refs,refs+copy.deepcopy(refs),refs+[dict(refs[0],value=99)]):
   unrelated=[dict(refs[0],measurement_timestamp='1970-01-01T00:00:05.000000001Z')]*1000
   population=unrelated+selected
   expected=e.compare_volume(obs,inputs,population,snapshots=snapshots)
   actual=e.compare_volume(obs,inputs,population,snapshots=snapshots,reference_index=e._volume_reference_index(population))
   self.assertEqual(actual,expected)
 def test_index_never_repairs_missing_measurement_or_wrong_identity(self):
  obs,inputs,refs,snapshots=fixtures.AccuracyEvidenceRepairs().volume()
  for bad in ([dict(refs[0],measurement_timestamp=None)],[dict(refs[0],pod_uid='replacement')],[dict(refs[0],node_uid='replacement')]):
   expected=e.compare_volume(obs,inputs,bad,snapshots=snapshots)
   self.assertEqual(expected['state'],'insufficient')
   self.assertEqual(e.compare_volume(obs,inputs,bad,snapshots=snapshots,reference_index=e._volume_reference_index(bad)),expected)
if __name__=='__main__':unittest.main()
