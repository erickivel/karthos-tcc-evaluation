import unittest
from evaluation.gke import evaluate as e
class HostReferenceIndexTests(unittest.TestCase):
 def test_candidates_preserve_exact_results_order_duplicates_and_conflicts(self):
  observed={'metric':'system.memory.usage','source':'host-os','node':'node','node_uid':'uid','dimensions':{'state':'free'},'unit':'bytes','value':1024,'kind':'sum','is_monotonic':False,'aggregation_temporality':2,'measurement_timestamp':10}
  row={'source':'linux-proc','node':'node','node_uid':'uid','metric':'memory.MemFree','dimensions':{},'unit':'kB','value':1,'measurement_timestamp':10,'observer_pod_uid':'ref','boot_id':'boot','acquisition_started_at':9,'acquisition_ended_at':11}
  for matched in ([row],[row,row],[row,dict(row,value=2)]):
   refs=[dict(row,metric='cpu.time')]*500+matched+[dict(row,node_uid='other')]
   expected=e.compare_reference(observed,refs);candidates=e._host_reference_candidates(observed,e._host_reference_index(refs));actual=e.compare_reference(observed,candidates)
   actual['reference_records_available']=len(refs)
   self.assertEqual(actual,expected);self.assertEqual(candidates,matched)
 def test_families_and_unknown_sources_keep_truthful_insufficiency(self):
  refs=[{'node_uid':'uid','source':'statvfs','metric':'filesystem.used'},{'node_uid':'uid','source':'statvfs','metric':'filesystem.available'},{'node_uid':'uid','source':'linux-proc','metric':'cpu.time'},{'node_uid':'uid','source':'linux-proc','metric':'disk.sectors'},{'node_uid':'uid','source':'linux-proc','metric':'network.bytes'}]
  index=e._host_reference_index(refs)
  for name,expected in [('system.filesystem.usage',refs[:2]),('system.filesystem.utilization',refs[:2]),('system.cpu.utilization',refs[2:3]),('system.disk.io.read',refs[3:4]),('system.disk.io.write',refs[3:4]),('system.network.io.receive',refs[4:5]),('system.network.io.transmit',refs[4:5])]:
   self.assertEqual(e._host_reference_candidates({'node_uid':'uid','metric':name},index),expected)
if __name__=='__main__':unittest.main()
