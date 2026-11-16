import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from evaluation import pilot
from evaluation.gke.report_summary import family_summaries
from evaluation.gke import evaluate
class AttemptRepairTests(unittest.TestCase):
 def test_terminating_old_uid_blocks_until_absent(self):
  old={'metadata':{'uid':'old','deletionTimestamp':'now'},'status':{'phase':'Running'}}
  with tempfile.TemporaryDirectory() as tmp,patch.object(pilot.r,'kube_json',side_effect=[{'items':[old]},{'items':[]}]),patch.object(pilot.time,'sleep') as sleep:
   pilot.wait_observer_termination('ns',{'old'},Path(tmp)/'receipt.json',timeout=2)
   self.assertEqual(sleep.call_count,1);self.assertTrue(json.loads((Path(tmp)/'receipt.json').read_text())['complete'])
 def test_same_name_new_uid_does_not_block(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(pilot.r,'kube_json',return_value={'items':[{'metadata':{'name':'same','uid':'new'}}]}):
   pilot.wait_observer_termination('ns',{'old'},Path(tmp)/'receipt.json',timeout=1)
 def test_timeout_preserves_outstanding_uid(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(pilot.r,'kube_json',return_value={'items':[{'metadata':{'uid':'old'}}]}):
   p=Path(tmp)/'receipt.json'
   with self.assertRaises(TimeoutError):pilot.wait_observer_termination('ns',{'old'},p,timeout=0)
   self.assertEqual(json.loads(p.read_text())['outstanding_uids'],['old'])
 def test_trace_settled_diagnostic_is_not_applicable_but_missing_stays_insufficient(self):
  r=family_summaries({'settled_duration_diagnostics':{'state':'not-applicable','reason':'trace populations checked directly'}})
  self.assertEqual(r['settled_traffic_duration']['state'],'not-applicable')
  self.assertEqual(family_summaries({})['settled_traffic_duration']['state'],'insufficient')
 def test_saved_inventory_attests_only_exact_uid_immutable_volume_declaration(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);pod={'metadata':{'uid':'pod','name':'app','namespace':'ns','resourceVersion':'7'},'spec':{'nodeName':'node','volumes':[{'name':'v','emptyDir':{}}]}}
   (p/'k8s-pods.json').write_text(json.dumps({'items':[pod]}))
   rows=evaluate._lifecycle_snapshots(p,agents={'agents':[{'node':'node','node_uid':'nodeuid'}]})
   self.assertEqual(rows[0]['items'][0]['metadata']['uid'],'pod');self.assertTrue(rows[0]['immutable_spec_declaration']);self.assertIsNone(rows[0].get('measurement_timestamp'))

 def test_foreign_replica_set_pods_do_not_enter_barrier(self):
  rs={'items':[{'metadata':{'uid':'rs','ownerReferences':[{'kind':'Deployment','uid':'deployment'}]}},{'metadata':{'uid':'foreign-rs','ownerReferences':[{'kind':'Deployment','uid':'other'}]}}]}
  pods={'items':[{'metadata':{'uid':'owned','ownerReferences':[{'kind':'ReplicaSet','uid':'rs'}]}},{'metadata':{'uid':'foreign','ownerReferences':[{'kind':'ReplicaSet','uid':'foreign-rs'}]}}]}
  with patch.object(pilot.r,'kube_json',side_effect=[rs,pods]):self.assertEqual(pilot.observer_prior_uids('ns',{'metadata':{'uid':'deployment'}}),{'owned'})
 def test_mesh_missing_duration_not_reclassified(self):
  r=family_summaries({'settled_duration_diagnostics':{'state':'insufficient','native_settled_to_declared_drain_export':{'state':'fail'}}})
  self.assertEqual(r['settled_traffic_duration']['state'],'fail')

 def test_live_volume_check_recovers_saved_declaration_and_rejects_conflict(self):
  from evaluation.tests.test_accuracy_evidence_repairs import AccuracyEvidenceRepairs
  obs,inputs,refs,previous=AccuracyEvidenceRepairs().volume()
  pod=previous[0]['items'][0];pod['metadata']['resourceVersion']='7'
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp);(path/'k8s-pods.json').write_text(json.dumps({'items':[pod]}))
   agents={'agents':[{'node':'n','node_uid':'nid'}]}
   snapshots=evaluate._lifecycle_snapshots(path,agents=agents,inputs=inputs)
   self.assertEqual(evaluate.compare_volume(obs,inputs,refs,snapshots=snapshots)['state'],'pass')
   pod['spec']['volumes'][0]={'name':'data','secret':{}}
   (path/'generator-pods.json').write_text(json.dumps({'items':[pod]}))
   snapshots=evaluate._lifecycle_snapshots(path,agents=agents,inputs=inputs)
   self.assertEqual(evaluate.compare_volume(obs,inputs,refs,snapshots=snapshots)['state'],'insufficient')
