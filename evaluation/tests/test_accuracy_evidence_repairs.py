"""Regression cases from the 2026-11-13 sealed Boutique evidence."""
import copy
import unittest
from evaluation.gke import evaluate as e
from evaluation.gke.report_summary import family_summaries


class AccuracyEvidenceRepairs(unittest.TestCase):
    def lifecycle(self, metric, unit):
        observed=dict(metric=metric,source='kube-cluster',pod_uid='uid',node='n',node_uid='nid',namespace='app',pod='p',container='c',container_id='abc',dimensions={},unit=unit,resource_version='7',value=2 if 'restart' in metric or 'phase' in metric else 1)
        pod={'metadata':{'uid':'uid','name':'p','namespace':'app','resourceVersion':'7'},'spec':{'nodeName':'n'},'status':{'phase':'Running','containerStatuses':[{'name':'c','containerID':'containerd://abc','ready':True,'restartCount':2}]}}
        snapshots=[{'items':[pod],'node_uids':{'n':'nid'},'acquisition_started_at':1,'acquisition_ended_at':2}]
        return observed,snapshots

    def test_pinned_native_lifecycle_units_and_runtime_id_alias(self):
        for metric,unit in [('k8s.pod.status.phase',None),('k8s.container.ready',None),('k8s.container.restart.count','{restart}')]:
            with self.subTest(metric=metric):
                obs,refs=self.lifecycle(metric,unit)
                self.assertEqual(e.compare_lifecycle(obs,refs)['state'],'pass')

    def test_bad_units_have_unit_reason_and_do_not_skip_version_gate(self):
        obs,refs=self.lifecycle('k8s.container.ready','seconds')
        self.assertIn('unit',e.compare_lifecycle(obs,refs)['reason'])
        obs['unit']=None;obs.pop('resource_version')
        self.assertIn('alignment',e.compare_lifecycle(obs,refs)['reason'])
        obs['resource_version']='7';obs['container_id']='replacement'
        self.assertEqual(e.compare_lifecycle(obs,refs)['state'],'insufficient')

    def volume(self):
        obs=dict(metric='k8s.pod.volume.available',source='kubelet',collector_uid='col',node='n',node_uid='nid',pod_uid='uid',pod='p',namespace='app',unit='By',recorded_timestamp=5,value=30,dimensions={'volume':'data','pvc_name':None,'pvc_namespace':None,'volume_type':'emptyDir'})
        vol={'name':'data','time':'1970-01-01T00:00:04Z','availableBytes':30}
        body={'node':{'nodeName':'n'},'pods':[{'podRef':{'namespace':'app','name':'p','uid':'uid'},'volume':[vol]}]}
        inputs=[dict(collector_uid='col',node_name='n',status=200,path='/stats/summary',requested_at_ns=4900000000,received_at_ns=5000000000,body=body)]
        refs=[dict(source='kubelet-summary',scope='volume',node='n',node_uid='nid',pod_uid='uid',pod='p',namespace='app',metric='fs.availableBytes',unit='bytes',dimensions={'volume':'data','pvc_name':None,'pvc_namespace':None},measurement_timestamp=vol['time'],acquisition_started_at=4.8,acquisition_ended_at=5.1,value=30)]
        pod={'metadata':{'namespace':'app','name':'p','uid':'uid'},'spec':{'nodeName':'n','volumes':[{'name':'data','emptyDir':{}}]}}
        snapshots=[dict(items=[pod],node_uids={'n':'nid'},acquisition_started_at=4,acquisition_ended_at=6)]
        return obs,inputs,refs,snapshots

    def test_non_pvc_type_is_proven_by_uid_scoped_pod_spec(self):
        obs,inputs,refs,snapshots=self.volume()
        self.assertEqual(e.compare_volume(obs,inputs,refs,snapshots=snapshots)['state'],'pass')
        self.assertEqual(e.compare_volume(obs,inputs,refs,snapshots=[])['state'],'insufficient')
        snapshots[0]['items'][0]['metadata']['uid']='replacement'
        self.assertEqual(e.compare_volume(obs,inputs,refs,snapshots=snapshots)['state'],'insufficient')

    def test_volume_ambiguity_wrong_type_and_bad_value_remain_visible(self):
        obs,inputs,refs,snapshots=self.volume()
        self.assertEqual(e.compare_volume(obs,inputs+copy.deepcopy(inputs),refs,snapshots=snapshots)['state'],'insufficient')
        obs['value']=31
        self.assertEqual(e.compare_volume(obs,inputs,refs,snapshots=snapshots)['state'],'fail')
        obs['value']=30;obs['dimensions']['volume_type']='secret'
        self.assertEqual(e.compare_volume(obs,inputs,refs,snapshots=snapshots)['state'],'insufficient')

    def test_cpu_pass_is_not_hidden_by_host_insufficiency(self):
        report={'resources':{'state':'pass','checks':[{'state':'pass','metric':'cpu'}]},'numerical_agreement':{'state':'insufficient','checks':[{'observed':{'metric':'system.cpu.utilization'},'state':'insufficient'}]}}
        families=family_summaries(report)
        self.assertEqual(families['container_resource_accuracy']['state'],'pass')
        self.assertEqual(families['host_resource_accuracy']['state'],'insufficient')
        self.assertEqual(families['resource_accuracy']['state'],'insufficient')

    def test_traffic_retains_numeric_comparison_without_trusting_boolean_population(self):
        traffic={'independent_valid':True,'api_storage_parity':{'checks':{'exact_request_count_api':{'state':'pass'}}}}
        verification={'source_population':{'matched':True},'counts_path':{'source_to_vm':{'state':'pass','reference':72,'observed':72}}}
        result=e.compare_traffic('servicegraph.request.count',traffic,verification)
        self.assertEqual(result['state'],'insufficient')
        self.assertEqual(result['independent_comparison']['state'],'pass')
        self.assertEqual(result['population_equivalence']['state'],'insufficient')
        self.assertIn('population',result['reason'])
        verification['counts_path']['source_to_vm']['state']='fail'
        self.assertEqual(e.compare_traffic('servicegraph.request.count',traffic,verification)['state'],'fail')

    def test_host_timestamp_is_not_manufactured(self):
        obs=dict(metric='system.network.io.receive',source='host-os',node='n',node_uid='nid',kind='sum',is_monotonic=True,aggregation_temporality=2,unit='bytes',value=10,dimensions={'interface':'eth0','direction':'receive'},recorded_timestamp=5,measurement_timestamp=None)
        refs=[dict(metric='network.bytes',source='linux-proc',node='n',node_uid='nid',boot_id='b',observer_pod_uid='o',unit='bytes',value=v,dimensions=obs['dimensions'],measurement_timestamp=None,acquisition_started_at=t,acquisition_ended_at=t+.1) for t,v in [(1,1),(8,20)]]
        self.assertIn('source read time',e.compare_reference(obs,refs)['reason'])
