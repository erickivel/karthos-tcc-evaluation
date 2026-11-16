import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from evaluation.gke import traffic_population as p


class TrafficPopulationTests(unittest.TestCase):
    def test_absence_and_observed_zero_replicas_pass(self):
        self.assertEqual(p.inspect('astronomy',{'items':[]},{'items':[]})['state'],'pass')
        deployment={'metadata':{'name':'load-generator'},'spec':{'replicas':0},'status':{'replicas':0}}
        self.assertEqual(p.inspect('astronomy',{'items':[deployment]},{'items':[]})['state'],'pass')
        deployment['status']['replicas']=1
        self.assertEqual(p.inspect('astronomy',{'items':[deployment]},{'items':[]})['state'],'fail')

    def test_known_running_or_terminating_pod_blocks_even_without_deployment(self):
        pod={'metadata':{'name':'anything','labels':{'app':'loadgenerator'},'deletionTimestamp':'now'},'status':{'phase':'Running'}}
        self.assertEqual(p.inspect('boutique',{'items':[]},{'items':[pod]})['state'],'fail')
        pod['status']['phase']='Succeeded'
        self.assertEqual(p.inspect('boutique',{'items':[]},{'items':[pod]})['state'],'pass')

    def test_unknown_workloads_not_classified_or_suppressed(self):
        deployment={'metadata':{'name':'my-loadgenerator-like-app'},'spec':{'replicas':1}}
        result=p.inspect('boutique',{'items':[deployment]},{'items':[]})
        self.assertEqual(result['state'],'pass')
        self.assertEqual(result['unclassified_deployments'],['my-loadgenerator-like-app'])
        self.assertFalse(result['all_traffic_attributed'])

    def test_probe_exclusion_is_only_a_declaration(self):
        pod={'metadata':{'name':'frontend'},'spec':{'containers':[{'name':'frontend','readinessProbe':{'httpGet':{'path':'/_healthz','port':8080}}}]}}
        result=p.inspect('boutique',{'items':[]},{'items':[pod]})
        self.assertEqual(result['declared_probe_routes'][0]['path'],'/_healthz')
        self.assertIsNone(result['excluded_request_count'])

    def test_incomplete_inventory_cannot_pass(self):
        for doc in ({},{'items':None},{'items':[{}]}):
            self.assertEqual(p.inspect('boutique',doc,{'items':[]})['state'],'insufficient')

    def test_failed_check_writes_receipt_and_raises(self):
        from evaluation.gke.models import write,read
        with tempfile.TemporaryDirectory() as tmp:
            ctx=SimpleNamespace(json=lambda *args:{'items':[{'metadata':{'name':'loadgenerator'},'spec':{'replicas':1}}]} if args[1]=='deployments' else {'items':[]},write=write)
            with self.assertRaises(ValueError):p.verify(ctx,SimpleNamespace(demo='boutique',namespace='app'),Path(tmp))
            self.assertEqual(read(Path(tmp)/'traffic-population-preflight.json')['state'],'fail')

    def test_zero_desired_replicas_requires_reconciled_generation(self):
        deployment={'metadata':{'name':'load-generator','generation':3},'spec':{'replicas':0},'status':{'observedGeneration':2}}
        self.assertEqual(p.inspect('astronomy',{'items':[deployment]},{'items':[]})['state'],'fail')
        deployment['status']['observedGeneration']=3
        self.assertEqual(p.inspect('astronomy',{'items':[deployment]},{'items':[]})['state'],'pass')

    def test_astronomy_exact_component_label_blocks_orphan_pod(self):
        pod={'metadata':{'name':'arbitrary-generated-name','labels':{'app.kubernetes.io/component':'load-generator'}},'status':{'phase':'Pending'}}
        self.assertEqual(p.inspect('astronomy',{'items':[]},{'items':[pod]})['state'],'fail')
        pod['metadata']['labels']['app.kubernetes.io/component']='load-generator-helper'
        self.assertEqual(p.inspect('astronomy',{'items':[]},{'items':[pod]})['state'],'pass')
