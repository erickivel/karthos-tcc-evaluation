"""Official claims require independent populations and comparable inputs."""
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from evaluation.gke import thesis, thesis_comparison as tc, report_summary, models

class OfficialClaimTests(unittest.TestCase):
    def test_excluded_claim_does_not_prevent_confirmation(self):
        self.assertTrue(thesis.required_claims_confirmed({'claims':[{'verdict':'pass'}, {'verdict':'not-applicable','required':False}]}))
        self.assertFalse(thesis.required_claims_confirmed({'claims':[{'verdict':'pass'},{'verdict':'insufficient'}]}))
        self.assertFalse(thesis.required_claims_confirmed({'claims':[]}))
        self.assertFalse(thesis.required_claims_confirmed({'claims':[{'verdict':'not-applicable','required':True}]}))

    def manifest(self):
        return {'schedule':'thesis-completion-v2','product_pins':{'karthos_revision':'a'*40},
                'implementation_hashes':{'evaluation/workloads/run.js':'a'*64,'evaluation/workloads/workload.json':'b'*64},
                'image_lock':{'collector':'c'*64},'baseline_config':{'telemetry_drain_seconds':90},
                'protocol_definition':{'baseline_rate':2},'thresholds':{'request_absolute_tolerance':2},
                'config':{'infrastructure':{'worker_count':3}}}

    def test_signature_binds_workload_and_collection_content(self):
        report={'spec':{'demo':'boutique','phase':'baseline','generator':'k6','rate':2,'duration_seconds':300,'scenario':'matched'}}
        original=tc.bind_comparison_inputs(copy.deepcopy(report), self.manifest())['comparison_inputs']
        for field in ('script','collection','threshold'):
            manifest=self.manifest()
            if field=='script':manifest['implementation_hashes']['evaluation/workloads/run.js']='f'*64
            if field=='collection':manifest['baseline_config']['telemetry_drain_seconds']=91
            if field=='threshold':manifest['thresholds']['request_absolute_tolerance']=3
            actual=tc.bind_comparison_inputs(copy.deepcopy(report), manifest)['comparison_inputs']
            self.assertNotEqual(original['sha256'],actual['sha256'])
        self.assertEqual(tc.input_agreement({'comparison_inputs':original},{'comparison_inputs':original})['state'],'pass')
        broken=copy.deepcopy(original);broken['definition']['thresholds']={}
        self.assertEqual(tc.input_agreement({'comparison_inputs':original},{'comparison_inputs':broken})['state'],'fail')
        self.assertEqual(tc.input_agreement({}, {})['state'],'insufficient')

    def test_common_graph_does_not_pass_two_identically_missing_services(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            models.write(p/'k8s-services.json',{'items':[{'metadata':{'namespace':'app','name':n}} for n in ('frontend','checkoutservice','catalog')]})
            nodes=[{'id':n,'name':n,'namespace':'app','kind':'service'} for n in ('frontend','checkoutservice')]
            models.write(p/'api-nodes.json',{'data':{'nodes':nodes}})
            models.write(p/'api-edges.json',{'data':{'edges':[{'source':'frontend','target':'checkoutservice','relation':'calls'}]}})
            value=tc.service_graph_evidence(p,'app','boutique')
            self.assertEqual(value['state'],'fail')
            self.assertEqual(value['missing_services'],['catalog'])
            nodes.append({'id':'catalog','name':'catalog','namespace':'app','kind':'service'})
            models.write(p/'api-nodes.json',{'data':{'nodes':nodes}})
            value=tc.service_graph_evidence(p,'app','boutique')
            self.assertEqual(value['state'],'pass')
            models.write(p/'api-edges.json',{'data':{'edges':[]}})
            self.assertEqual(tc.service_graph_evidence(p,'app','boutique')['state'],'fail')

    def test_duration_requires_source_matched_population_not_just_parity(self):
        report={'families':{'traffic_duration':{'state':'insufficient'},'exact_api_storage_parity':{'state':'pass'}}}
        self.assertEqual(tc.duration_evidence(report)['state'],'insufficient')
        report['families'].update(settled_traffic_duration={'state':'pass'},evidence_horizon={'state':'pass'})
        self.assertEqual(tc.duration_evidence(report)['state'],'pass')
        report['families']['traffic_duration']['state']='fail'
        self.assertEqual(tc.duration_evidence(report)['state'],'fail')

    def test_v2_claims_have_no_historical_placeholder_and_exclude_exact_alignment(self):
        ctx=SimpleNamespace(manifest={'schedule':'thesis-completion-v2','matrix':[],
                            'product_pins':{'karthos_revision':'a'*40}},state={})
        rows={r['claim']:r for r in thesis.claim_matrix(ctx,[],None,None)['claims']}
        self.assertNotIn('cross-deployment-reproducibility',rows)
        self.assertIn('clean-rebuild-boutique-trace',rows)
        self.assertEqual(rows['host-lifecycle-exact-alignment']['verdict'],'not-applicable')
        self.assertFalse(rows['host-lifecycle-exact-alignment']['required'])
        self.assertEqual(rows['fresh-cluster-identities']['verdict'],'insufficient')
        ctx.state={'evaluation_cluster_instances':[
            {'repeat':1,'resources':[{'kind':'cluster','uid':'one'}]},
            {'repeat':2,'resources':[{'kind':'cluster','uid':'two'}]}]}
        rows={r['claim']:r for r in thesis.claim_matrix(ctx,[],None,None)['claims']}
        self.assertEqual(rows['fresh-cluster-identities']['verdict'],'pass')

    def test_strict_comparison_requires_duration_and_error_evidence(self):
        from unittest.mock import patch
        matrices=[{'id':'a','repeat':1,'cell':'boutique-trace','phase':'baseline'},
                  {'id':'b','repeat':1,'cell':'boutique-mesh','phase':'baseline'}]
        reports={key:('gs://test/'+key,{'path':'/missing','spec':{'demo':'boutique'},'families':{}}) for key in ('a','b')}
        with patch.object(tc,'_observation',return_value={'reference_calls':100,'settled_calls':100,'ratio':1}):
            result=tc.compare_pairs(matrices,reports,strict=True)[0]
        self.assertEqual(result['details']['duration_populations']['state'],'insufficient')
        self.assertEqual(result['details']['source_owned_errors']['state'],'insufficient')
        self.assertEqual(result['verdict'],'insufficient')

class BoundaryTests(unittest.TestCase):
    def test_missing_boundary_is_not_inferred_from_later_agreement(self):
        from evaluation.gke.boundary_summary import summarize
        report={'manifest':{'start':10,'end':20},'traffic':{'report':{}}}
        verification={'counts_path':{'source_to_vm':{'state':'fail','reference':600,'observed':580},
                                    'exact_request_count_api':{'state':'pass'}}}
        result=summarize(report,verification)
        self.assertEqual(result['first_demonstrated_discrepancy']['boundary'],'source-to-storage')
        self.assertFalse(result['upstream_complete'])
        self.assertEqual(result['cause'],'exact cause unknown')
        self.assertIsNone(result['boundaries'][0]['expected'])
        self.assertEqual(result['workload_bounds'],[10,20])

    def test_failed_capture_prevents_numerical_root_cause_claim(self):
        from evaluation.gke.boundary_summary import summarize
        r=summarize({'evidence':{'valid':False}}, {'counts_path':{'source_to_vm':{'state':'fail','reference':10,'observed':8}}})
        self.assertEqual(r['interpretation'],'invalid required evidence; retained numerical discrepancies are diagnostic only')
        self.assertEqual(r['cause'],'exact cause unknown')


    def test_html_and_bounded_summary_retain_explanations_and_escape_input(self):
        from evaluation.gke.boundary_summary import summarize, render
        report={'manifest':{'start':1,'end':2},'evidence':{'valid':True},
                'spec':{'phase':'baseline'},'execution':{'state':'complete'}}
        report['boundary_summary']=summarize(report, {'counts_path':{'source_to_vm':{
            'state':'fail','reference':10,'observed':8,'reason':'<unsafe>'}}})
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);models.write(root/'gke-evaluation.json',report)
            summary=report_summary.save_summary(root,report)
            self.assertEqual(summary['boundary_summary'],report['boundary_summary'])
            self.assertTrue((root/'boundary-summary.html').is_file())
            html=(root/'boundary-summary.html').read_text()
            self.assertNotIn('<unsafe>',html)
            self.assertIn('&lt;unsafe&gt;',html)

if __name__=='__main__':unittest.main()

class CompleteClaimMatrixTests(unittest.TestCase):
    def test_all_required_claims_can_pass_and_missing_duration_prevents_it(self):
        from unittest.mock import patch
        root_repo=Path(__file__).resolve().parents[2]
        matrix=models.build_matrix(models.read(root_repo/'protocols/thesis-completion-v2/protocol.yaml'),schedule=models.COMPLETION_V2)
        pin=OfficialClaimTests().manifest()
        pin.update(matrix=matrix,local_contract_evidence={'checks':{k:{'state':'pass'} for k in thesis.LOCAL_CHECKS}})
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);windows=[];reports=[]
            for spec in matrix:
                if spec['phase']=='smoke':continue
                path=root/spec['id'];path.mkdir()
                namespace='app-'+spec['demo'];target='checkout' if spec['demo']=='astronomy' else 'checkoutservice'
                source='mesh.istio' if spec['source']=='mesh' else 'traces.servicegraph'
                nodes=[{'id':name,'kind':'service','name':name,'namespace':namespace} for name in ('frontend',target)]
                models.write(path/'consumer/ledger.json',{'openapi_validated':True,'after':{'nodes':nodes}})
                models.write(path/'k8s-services.json',{'items':[{'metadata':{'name':n['name'],'namespace':namespace}} for n in nodes]})
                models.write(path/'api-nodes.json',{'data':{'nodes':nodes}})
                models.write(path/'api-edges.json',{'data':{'edges':[{'source':'frontend','target':target,'relation':'calls'}]}})
                families={k:{'state':'pass'} for k in ('traffic_count','traffic_error','traffic_duration','container_resource_accuracy','volume_accuracy')}
                report={'path':str(path),'spec':spec,'execution':{'state':'complete'},'evidence':{'valid':True},
                        'manifest':{'namespace':namespace,'source_id':source},'source_selection':{'state':'pass','expected_source':source},
                        'families':families,'coverage':{'contract':{'valid':True}},
                        'thesis_answers':{key:{'state':'correct'} for key in ('interval-callers','current-replicas-and-placement','container-resource-history','request-error-derivation','request-error-numerical-agreement')},
                        'traffic':{'report':{'recorder_identity_valid':True,
                            'windows':{'whole':{'checkout_place_order':{'evidence_present':True,'completion':{'requests':2}}}},
                            'eventual_completeness':{'state':'available','requests':2},
                            'whole_capture_source_identity_coverage':{'checkout_trace_ids':['t1','t2'],'paired_trace_ids':['t1','t2']},
                            'generator_contract':{'checkout_request_ids':['r1','r2'],'conflicting_request_ids':[]}}}}
                if spec.get('scenario')=='replanned-trace':report['arbitration_transition']={'mode':'rediscover-redeploy','runtime_failover_claim':False,'events':[{'step':step} for step in ('istio-unavailable','rediscovered','redeployed')]}
                tc.bind_comparison_inputs(report,pin)
                report['service_graph_evidence']=tc.service_graph_evidence(path,namespace,spec['demo'])
                models.write(path/'gke-evaluation.json',report)
                reports.append((path,report))
                windows.append({'id':spec['id'],'path':str(path),'execution':'complete','evidence_valid':True,'export':{'verified':True,'uri':'gs://test/'+spec['id']}})
            ctx=SimpleNamespace(manifest=pin,state={'evaluation_cluster_instances':[
                {'repeat':1,'resources':[{'kind':'cluster','uid':'cluster-1'}]},
                {'repeat':2,'resources':[{'kind':'cluster','uid':'cluster-2'}]}]})
            history={'answer':{'state':'correct'},'export':{'verified':True,'uri':'gs://test/history'},'continuation_safe':True}
            # Structural normalization is independently covered by its existing
            # graph fixtures; this test exercises claim composition and scope.
            with patch.object(tc,'_structure',return_value={'logical_structure':'same'}):
                result=thesis.claim_matrix(ctx,windows,history,{'ready':True})
                self.assertTrue(thesis.required_claims_confirmed(result),result)
                path,report=next((p,r) for p,r in reports if r['spec']['phase']=='baseline')
                report['families']['traffic_duration']['state']='insufficient'
                models.write(path/'gke-evaluation.json',report)
                result=thesis.claim_matrix(ctx,windows,history,{'ready':True})
                self.assertFalse(thesis.required_claims_confirmed(result))

class DetailedBoundaryTests(unittest.TestCase):
    def test_nested_parity_keeps_partition_values_and_frontend_identity_counts(self):
        from evaluation.gke.boundary_summary import summarize
        v={'frontend':{'state':'pass','observed':2,'checks':[
                {'request_id':'r1','observer_status':200,'state':'pass'},
                {'request_id':'r2','observer_status':500,'state':'pass'}]},
           'counts_path':{'exact_request_count_api':{'state':'pass','checks':[
                {'reference':290,'observed':290,'timestamp':100,'labels':{'grpc_status':'0'},'state':'pass'},
                {'reference':58,'observed':58,'timestamp':100,'labels':{'grpc_status':'13'},'state':'pass'}]}},
           'source':{'series':[{'first':1,'last':101,'metric':'requests','labels':{'grpc_status':'0'}}]},
           'resources':{'checks':[{'metric':'cpu','state':'pass','container':['app','pod','container'],
                        'independent_agreement':{'state':'pass','reference':.2,'observed':.2},'measurement_timestamp':25}]}}
        result=summarize({'manifest':{'start':2,'end':100}},v)
        front=result['boundaries'][0]
        self.assertEqual((front['expected'],front['observed']),(2,2))
        parity=next(r for r in result['boundaries'] if r['boundary']=='storage-to-api')
        self.assertIsNone(parity['expected']) # Never sum unlike or repeated partitions.
        self.assertEqual(parity['comparison_count'],2)
        self.assertEqual(parity['examples'][1]['reference'],58)
        self.assertEqual(parity['examples'][1]['timestamp'],100)
        self.assertEqual(result['source_counter_brackets']['examples'][0]['first'],1)
        self.assertEqual(result['resource_comparisons'][0]['eligible_aligned_measurements'],1)
        self.assertIn('structured/status.json',result['diagnostic_evidence'])

    def test_nested_examples_are_bounded_without_losing_denominator(self):
        from evaluation.gke.boundary_summary import summarize
        checks=[{'state':'pass','reference':n,'observed':n,'timestamp':n} for n in range(1000)]
        result=summarize({}, {'counts_path':{'exact_request_count_api':{'state':'pass','checks':checks}}})
        parity=next(r for r in result['boundaries'] if r['boundary']=='storage-to-api')
        self.assertEqual(parity['comparison_count'],1000)
        self.assertLessEqual(len(parity['examples']),8)
        self.assertEqual(parity['omitted_examples'],992)
