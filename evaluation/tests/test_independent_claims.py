import copy
import json
import tempfile
import unittest
from pathlib import Path


class IndependentStructureTests(unittest.TestCase):
    def evidence(self, root):
        objects = {
            'k8s-pods.json': {'items': [dict(metadata=dict(name='pod-a', namespace='app', uid='uid-a'), spec=dict(nodeName='worker', containers=[dict(name='app')]))]},
            'k8s-services.json': {'items': [dict(metadata=dict(name='checkout', namespace='app', uid='svc-a'))]},
            'k8s-nodes.json': {'items': [dict(metadata=dict(name='worker', uid='node-a'))]},
            'api-nodes.json': {'data': {'nodes': [dict(id='p', kind='pod', name='pod-a', namespace='app'), dict(id='s', kind='service', name='checkout', namespace='app'), dict(id='n', kind='node', name='worker'), dict(id='c', kind='container', name='app', namespace='app', labels={'k8s.pod.name':'pod-a'})]}},
            'api-edges.json': {'data': {'edges': [dict(source='p', target='n', relation='runs_on'), dict(source='p', target='c', relation='contains')]}}
        }
        for name, value in objects.items(): (root/name).write_text(json.dumps(value))
        return objects

    def test_independent_inventory_and_negative_relation(self):
        from evaluation.gke.independent_claims import structural_fidelity
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); docs=self.evidence(root)
            result=structural_fidelity(root,'app')
            self.assertEqual(result['state'],'pass')
            self.assertFalse(result['all_entity_kinds_verified'])
            docs['api-edges.json']['data']['edges'][0]['relation']='contains'
            (root/'api-edges.json').write_text(json.dumps(docs['api-edges.json']))
            self.assertEqual(structural_fidelity(root,'app')['state'],'fail')
            (root/'k8s-pods.json').unlink()
            self.assertEqual(structural_fidelity(root,'app')['state'],'insufficient')

    def test_missing_or_duplicate_entity_cannot_pass(self):
        from evaluation.gke.independent_claims import structural_fidelity
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); docs=self.evidence(root)
            nodes=docs['api-nodes.json']['data']['nodes'];nodes.append(dict(nodes[0],id='duplicate'))
            (root/'api-nodes.json').write_text(json.dumps(docs['api-nodes.json']))
            self.assertEqual(structural_fidelity(root,'app')['state'],'fail')

    def test_unavailable_pod_preserves_established_contradiction(self):
        from evaluation.gke.independent_claims import structural_fidelity
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); docs=self.evidence(root)
            docs['api-nodes.json']['data']['nodes'].pop(1)
            docs['k8s-pods.json']['items'].append(dict(metadata=dict(name='done', namespace='app', uid='done'), spec={}, status=dict(phase='Succeeded')))
            for name,value in docs.items(): (root/name).write_text(json.dumps(value))
            result=structural_fidelity(root,'app')
            self.assertEqual(result['state'],'fail')
            self.assertIn('insufficient',[c['state'] for c in result['checks']])


class DoubleCountingTests(unittest.TestCase):
    def test_no_double_counting_claim_requires_both_available_window(self):
        from evaluation.gke.independent_claims import no_double_counting
        record=self.record();record['families']={'traffic_count':{'state':'pass'}}
        matrix=[dict(id='both',phase='arbitration',scenario='both-sources')]
        self.assertEqual(no_double_counting(matrix,{'both':('gs://both',record)})['verdict'],'pass')
        self.assertEqual(no_double_counting(matrix,{})['verdict'],'insufficient')
        record['traffic']['report']['eventual_completeness']['requests']=200
        self.assertEqual(no_double_counting(matrix,{'both':('gs://both',record)})['verdict'],'fail')

    def record(self):
        return dict(evidence={'valid':True}, manifest={'source_id':'mesh.istio'},
            source_selection={'state':'pass','expected_source':'mesh.istio'},
            traffic={'report':{'recorder_identity_valid':True,
                'windows':{'whole':{'checkout_place_order':{'evidence_present':True,'completion':{'requests':100}}}},
                'eventual_completeness':{'state':'available','requests':100}}})

    def test_double_count_and_wrong_source_rejected_missing_reference_inconclusive(self):
        from evaluation.gke.independent_claims import single_contribution
        record=self.record()
        self.assertEqual(single_contribution(record)['state'],'pass')
        record['traffic']['report']['eventual_completeness']['requests']=200
        self.assertEqual(single_contribution(record)['state'],'fail')
        record=self.record();record['manifest']['source_id']='traces.servicegraph'
        self.assertEqual(single_contribution(record)['state'],'fail')
        record=self.record();record['traffic']['report']['windows']={}
        self.assertEqual(single_contribution(record)['state'],'insufficient')
