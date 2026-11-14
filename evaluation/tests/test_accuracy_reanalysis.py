import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from evaluation.gke import accuracy_reanalysis as r


class AccuracyReanalysisTests(unittest.TestCase):
    def archive(self,root,*,corrupt=False,report=None,extras=None):
        old={'spec':{'id':'smoke'},'resources':{'state':'pass','checks':[{'metric':'cpu','state':'pass'}]},'selected_accuracy':[{'name':'servicegraph.request.count','state':'insufficient','checks':[]}],'numerical_agreement':{'state':'insufficient','checks':[]},'traffic':{'report':{'independent_valid':True}},'families':{}}
        old=report or old
        data={'gke-evaluation.json':json.dumps(old).encode(),'verification.json':b'{"counts_path":{"source_to_vm":{"state":"pass"}}}'}
        data.update(extras or {})
        seals=''.join(hashlib.sha256(v).hexdigest()+'  '+k+'\n' for k,v in data.items()).encode()
        if corrupt:data['verification.json']=b'{}'
        data['GKE-SHA256SUMS']=seals
        path=root/'source.tar.gz'
        with tarfile.open(path,'w:gz') as tar:
            for name,value in data.items():
                m=tarfile.TarInfo(name);m.size=len(value);tar.addfile(m,io.BytesIO(value))
        return path,hashlib.sha256(path.read_bytes()).hexdigest()

    def test_archive_reanalysis_is_read_only_and_versioned(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);archive,sha=self.archive(root);before=archive.read_bytes()
            report=r.reanalyse_archive(archive,root/'analysis',expected_sha256=sha)
            self.assertEqual(archive.read_bytes(),before)
            self.assertEqual(report['analysis_version'],r.VERSION)
            self.assertEqual(report['families']['container_resource_accuracy']['state'],'pass')
            self.assertEqual(report['families']['traffic_count']['state'],'insufficient')
            self.assertEqual(report['families']['traffic_count_comparison']['state'],'pass')
            self.assertTrue((root/'analysis/analysis-manifest.json').is_file())
            with self.assertRaises(ValueError):r.reanalyse_archive(archive,root/'analysis',expected_sha256=sha)

    def test_archive_and_member_checksum_mismatch_do_not_publish(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);archive,sha=self.archive(root,corrupt=True)
            for digest in ['0'*64,sha]:
                with self.assertRaises(ValueError):r.reanalyse_archive(archive,root/'analysis',expected_sha256=digest)
                self.assertFalse((root/'analysis').exists())

    def test_volume_reanalysis_requires_independent_node_uid(self):
        from evaluation.tests.test_accuracy_evidence_repairs import AccuracyEvidenceRepairs
        obs,inputs,refs,snapshots=AccuracyEvidenceRepairs().volume()
        body=inputs[0]['body']
        original={'spec':{},'resources':{},'selected_accuracy':[], 'numerical_agreement':{'state':'insufficient','checks':[{'state':'insufficient','observed':obs}]},'traffic':{}}
        for node_uid,expected in [('nid','pass'),(None,'insufficient')]:
            with self.subTest(node_uid=node_uid),tempfile.TemporaryDirectory() as d:
                root=Path(d)
                independent={'node_name':'n','node_uid':node_uid,'summary':body,'requested_at_ns':4800000000,'received_at_ns':5100000000}
                if node_uid is None:independent.pop('node_uid')
                extras={'extended/kubernetes-inventory.json':json.dumps({'snapshots':snapshots}).encode(),
                    'structured/agent/n/raw/receiver-input.ndjson':(json.dumps(inputs[0])+'\n').encode(),
                    'kubelet-n.ndjson':(json.dumps(independent)+'\n').encode()}
                archive,sha=self.archive(root,report=original,extras=extras)
                summary=r.reanalyse_archive(archive,root/'analysis',expected_sha256=sha)
                self.assertEqual(summary['families']['volume_accuracy']['state'],expected)

    def test_frozen_demo_recovers_a_positive_traffic_verdict_from_sealed_archive(self):
        from evaluation.tests import test_offline_population as fixtures
        traffic,verification=fixtures.PopulationTests().fixture()
        traffic['manifest'].pop('demo')
        traffic['api_storage_parity']={'checks':{'exact_request_count_api':{'state':'pass'}}}
        verification['counts_path']={'source_to_vm':{'state':'pass'}}
        old={'spec':{'demo':'boutique'},'resources':{},'selected_accuracy':[{'name':'servicegraph.request.count'}],
             'numerical_agreement':{'checks':[]},'traffic':{'report':traffic}}
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);archive,sha=self.archive(root,report=old,extras={'verification.json':json.dumps(verification).encode()})
            summary=r.reanalyse_archive(archive,root/'analysis',expected_sha256=sha)
            self.assertEqual(summary['families']['traffic_count']['state'],'pass')
            self.assertEqual(summary['traffic']['servicegraph.request.count'][0]['population_equivalence']['independent_reference']['target'],'checkoutservice')

    def test_receiver_pods_inventory_recovers_unread_system_volume_declaration(self):
        from evaluation.tests import test_accuracy_evidence_repairs as fixtures
        obs,inputs,refs,snapshots=fixtures.AccuracyEvidenceRepairs().volume()
        pod=snapshots[0]['items'][0];pod['metadata']['resourceVersion']='7'
        pods=dict(inputs[0],path='/pods',body={'items':[pod]})
        original={'spec':{},'resources':{},'selected_accuracy':[], 'numerical_agreement':{'state':'insufficient','checks':[{'state':'insufficient','observed':obs}]},'traffic':{}}
        independent={'node_name':'n','node_uid':'nid','summary':inputs[0]['body'],'requested_at_ns':4800000000,'received_at_ns':5100000000}
        extras={'structured/nodes-before.json':json.dumps({'items':[{'metadata':{'name':'n','uid':'nid'}}]}).encode(),
                'structured/agent/n/raw/receiver-input.ndjson':('\n'.join(json.dumps(x) for x in inputs+[pods])+'\n').encode(),
                'kubelet-n.ndjson':(json.dumps(independent)+'\n').encode()}
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);archive,sha=self.archive(root,report=original,extras=extras)
            summary=r.reanalyse_archive(archive,root/'analysis',expected_sha256=sha)
            self.assertEqual(summary['families']['volume_accuracy']['state'],'pass')
            self.assertEqual(summary['recovery_inventory']['receiver_pod_snapshots'],1)
