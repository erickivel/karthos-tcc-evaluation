import copy
import unittest
from evaluation.gke import accuracy_reanalysis as a
from evaluation.gke import evaluate as e
from evaluation.tests import test_accuracy_evidence_repairs as fixtures


class InventoryRecoveryTests(unittest.TestCase):
    def test_sealed_pod_declarations_recover_type_without_fabricating_read_time(self):
        obs,inputs,refs,snapshots=fixtures.AccuracyEvidenceRepairs().volume()
        doc={'items':snapshots[0]['items']};doc['items'][0]['metadata']['resourceVersion']='7'
        recovered=a.inventory_declarations(doc,{'n':'nid'},'sealed/pods-before.json')
        self.assertNotIn('acquisition_started_at',recovered)
        self.assertEqual(e.compare_volume(obs,inputs,refs,snapshots=[recovered])['state'],'pass')
        recovered['items'][0]['metadata']['uid']='replacement'
        self.assertEqual(e.compare_volume(obs,inputs,refs,snapshots=[recovered])['state'],'insufficient')

    def test_identical_cached_receiver_measurement_is_not_two_measurements(self):
        obs,inputs,refs,snapshots=fixtures.AccuracyEvidenceRepairs().volume()
        cached=copy.deepcopy(inputs[0]);cached.update(requested_at_ns=5100000000,received_at_ns=5200000000)
        result=e.compare_volume(obs,inputs+[cached],refs,snapshots=snapshots)
        self.assertEqual(result['state'],'pass',result)
        self.assertEqual(result['receiver_observation_count'],2)
        cached['body']['pods'][0]['volume'][0]['time']='1970-01-01T00:00:03Z'
        self.assertEqual(e.compare_volume(obs,inputs+[cached],refs,snapshots=snapshots)['state'],'insufficient')

    def test_lifecycle_diagnostics_recover_presence_but_never_collector_version(self):
        row={'observed_at':5,'uid':'uid','namespace':'app','name':'p','node':'n','phase':'Running','containers':[{'name':'c','containerID':'containerd://abc','ready':True,'restartCount':2,'status_kind':'containerStatuses'}]}
        snapshots=a.lifecycle_snapshots([row],[{'observed_at':6,'iteration_seconds':2}],{'n':'nid'})
        self.assertEqual(len(snapshots),1)
        self.assertEqual(snapshots[0]['acquisition_started_at'],4)
        self.assertNotIn('resourceVersion',snapshots[0]['items'][0]['metadata'])
        obs={'metric':'k8s.container.restart.count','value':2,'source':'kube-cluster','unit':'{restart}','pod_uid':'uid','namespace':'app','pod':'p','node':'n','node_uid':'nid','container':'c','container_id':'abc','dimensions':{}}
        result=e.compare_lifecycle(obs,snapshots)
        self.assertEqual(result['state'],'insufficient')
        self.assertIn('version/source-time',result['reason'])
        self.assertEqual(a.lifecycle_snapshots([row],[],{'n':'nid'}),[])
