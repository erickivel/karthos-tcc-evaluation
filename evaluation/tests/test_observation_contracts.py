import copy
import unittest
from evaluation.gke import report_summary
from evaluation.gke.evaluate import compare_lifecycle


class ObservationContractsTests(unittest.TestCase):
    def test_mesh_primary_uses_settled_population_and_keeps_fixed_window(self):
        report={'manifest':{'source_id':'mesh.istio'},'selected_accuracy':[{'name':'servicegraph.request.duration','state':'fail'}],
                'evidence_horizon':{'state':'pass'},'settled_duration_diagnostics':{'native_settled_to_declared_drain_export':{'state':'pass'}}}
        before=copy.deepcopy(report)
        families=report_summary.family_summaries(report)
        self.assertEqual(families['traffic_duration']['state'],'pass')
        self.assertEqual(families['fixed_window_traffic_duration']['state'],'fail')
        self.assertEqual(report,before)
        report['evidence_horizon']['state']='insufficient'
        self.assertEqual(report_summary.family_summaries(report)['traffic_duration']['state'],'insufficient')
        report['settled_duration_diagnostics']['native_settled_to_declared_drain_export']['state']='fail'
        self.assertEqual(report_summary.family_summaries(report)['traffic_duration']['state'],'fail')

    def test_missing_settled_never_falls_back_to_fixed_window_pass(self):
        report={'manifest':{'source_id':'mesh.istio'},'selected_accuracy':[{'name':'servicegraph.request.duration','state':'pass'}]}
        self.assertEqual(report_summary.family_summaries(report)['traffic_duration']['state'],'insufficient')

    def lifecycle(self):
        observed=dict(metric='k8s.pod.status.phase',value=2,unit='count',source='kube-cluster',namespace='app',pod='pod',pod_uid='uid',node='n',node_uid='node',dimensions={},recorded_timestamp=12)
        def snapshot(a,b,phase):
            return {'items':[{'metadata':{'name':'pod','namespace':'app','uid':'uid'},'spec':{'nodeName':'n'},'status':{'phase':phase}}], 'node_uids':{'n':'node'},'acquisition_started_at':a,'acquisition_ended_at':b}
        return observed,[snapshot(1,2,'Pending'),snapshot(4,5,'Running')]

    def test_bounded_lifecycle_transition_is_not_exact_accuracy(self):
        observed,snapshots=self.lifecycle();result=compare_lifecycle(observed,snapshots)
        self.assertEqual(result['state'],'insufficient')
        bounded=result['bounded_observation']
        self.assertEqual(bounded['state'],'observed')
        self.assertEqual(bounded['transitions'][0]['observation_interval_seconds'],[1,5])
        self.assertFalse(bounded['exact_alignment_claimed'])

    def test_overlap_replacement_and_receiver_input_do_not_establish_transition(self):
        for case in ('overlap','replacement','receiver'):
            observed,snapshots=self.lifecycle()
            if case=='overlap':snapshots[1]['acquisition_started_at']=1.5
            if case=='replacement':snapshots[1]['items'][0]['metadata']['uid']='other'
            if case=='receiver':snapshots[1]['receiver_collector_uid']='collector'
            result=compare_lifecycle(observed,snapshots)
            self.assertEqual(result['bounded_observation']['transitions'],[],case)

    def test_host_observations_are_descriptive_and_bounded(self):
        from evaluation.gke.observation_contracts import summarize
        checks=[{'state':'insufficient','observed':{'metric':'system.memory.usage','value':v,'recorded_timestamp':t,'measurement_timestamp':None}} for t,v in ((1,3),(2,8))]
        result=summarize(checks)
        row=result['host']['metrics'][0]
        self.assertEqual(row['observations'],2)
        self.assertEqual(row['collection_timestamp_range_seconds'],[1,2])
        self.assertFalse(result['host']['exact_alignment_claimed'])
        self.assertNotIn('value_range',row) # Distinct labels/units are never pooled numerically.

    def test_transition_output_is_capped_and_decreases_are_not_counter_proof(self):
        from evaluation.gke.observation_contracts import lifecycle_observations
        candidates=[({'acquisition_started_at':i*3,'acquisition_ended_at':i*3+1},{},i%2) for i in range(40)]
        result=lifecycle_observations(candidates)
        self.assertEqual(len(result['transitions']),16)
        self.assertEqual(result['transition_observations'],39)
        self.assertEqual(result['omitted_transition_observations'],23)
        self.assertEqual(result['state'],'observed')

    def test_trace_duration_retains_existing_population_verifier(self):
        report={'manifest':{'source_id':'traces.servicegraph'},'selected_accuracy':[{'name':'servicegraph.request.duration','state':'pass'}]}
        self.assertEqual(report_summary.family_summaries(report)['traffic_duration']['state'],'pass')
