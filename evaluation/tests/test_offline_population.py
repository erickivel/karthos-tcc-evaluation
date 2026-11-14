import copy
import unittest
from evaluation.gke import population as p


class PopulationTests(unittest.TestCase):
    def fixture(self):
        labels={'reporter':'destination','source_workload':'frontend','source_canonical_service':'frontend','source_workload_namespace':'app','destination_service_name':'checkoutservice','destination_canonical_service':'checkoutservice','destination_service_namespace':'app','request_protocol':'grpc','response_code':'200','grpc_response_status':'0'}
        stored={'__name__':'servicegraph_request_count','edge_client':'frontend','edge_server':'checkoutservice','client_namespace':'app','server_namespace':'app','source':'mesh.istio','network.protocol.name':'grpc','http.response.status_code':'200','rpc.grpc.status_code':'0'}
        obs={'identity':'o','boundary':'checkout','uri':'/PlaceOrder','start':12,'completion':13,'outcome_failed':False,'status':200,'grpc_status':'0'}
        native={'metric':'istio_requests_total','labels':labels,'first':9,'last':21,'increase':1,'resets':0}
        vm={'metric':stored,'first':8,'last':22,'increase':1,'resets':0}
        t={'manifest':{'demo':'boutique','source_id':'mesh.istio','namespace':'app'},'recorder_identity_valid':True,'independent_valid':True,'required_evidence':{'valid':True},'clock':{'uncertainty_seconds':.001},'structured_capture':{'started_at':0,'finished_at':30,'valid':True},'capture_integrity':{'valid':True},'events':{'observers':[obs],'traces':[]},'windows':{'whole':{'bounds_seconds':[10,20],'raw_mesh_source':{'series':[native]}}}}
        v={'source':{'series':[native]},'vm':{'series':[vm]}}
        return t,v

    def test_scoped_mesh_count_proof_has_positive_path(self):
        t,v=self.fixture();result=p.prove('servicegraph.request.count',t,v)
        self.assertEqual(result['state'],'pass',result)
        self.assertFalse(result['individual_stored_request_ids_supported'])
        self.assertEqual(result['expected_requests'],1)

    def test_recover_demo_only_from_explicit_frozen_spec_and_reject_conflict(self):
        t,v=self.fixture();t['manifest'].pop('demo')
        self.assertEqual(p.prove('servicegraph.request.count',t,v)['state'],'insufficient')
        recovered=p.resolve_scope(t,{'demo':'boutique'})
        self.assertEqual(p.prove('servicegraph.request.count',recovered,v)['state'],'pass')
        self.assertNotIn('demo',t['manifest'])
        with self.assertRaises(ValueError):p.resolve_scope(recovered,{'demo':'astronomy'})

    def test_extra_call_in_counter_slack_blocks_membership_proof(self):
        t,v=self.fixture();extra=dict(t['events']['observers'][0],identity='outside',start=20.5,completion=21)
        t['events']['observers'].append(extra)
        self.assertEqual(p.prove('servicegraph.request.count',t,v)['state'],'insufficient')

    def test_resets_status_partition_changes_and_duplicate_series_are_not_hidden(self):
        for mutation in ('reset','status','duplicate'):
            with self.subTest(mutation=mutation):
                t,v=self.fixture();row=v['vm']['series'][0]
                if mutation=='reset':row['resets']=1
                elif mutation=='status':row['metric']['rpc.grpc.status_code']='13'
                else:v['vm']['series'].append(copy.deepcopy(row))
                self.assertEqual(p.prove('servicegraph.request.count',t,v)['state'],'insufficient')

    def test_duration_needs_histogram_population_not_request_total(self):
        t,v=self.fixture();source=dict(v['source']['series'][0],metric='istio_request_duration_milliseconds_count');stored=dict(v['vm']['series'][0],metric=dict(v['vm']['series'][0]['metric'],__name__='servicegraph_request_duration_count'))
        v['source']['series'].append(source);v['vm']['series'].append(stored)
        self.assertEqual(p.prove('servicegraph.request.duration',t,v)['state'],'pass')
        source['increase']=0
        self.assertEqual(p.prove('servicegraph.request.duration',t,v)['state'],'insufficient')

    def test_trace_count_requires_generator_observer_and_parent_identity_join(self):
        t,v=self.fixture();t['manifest']['source_id']='traces.servicegraph'
        trace={'identity':'a/b','trace_id':'a'*32,'target_service':'checkoutservice','start':12,'completion':13,'paired_frontend':True,'conflicting_identity':False,'connector_failed':False}
        t['events']['traces']=[trace];t['events']['observers'][0]['traceparent']='00-'+'a'*32+'-'+'b'*16+'-01'
        t['whole_capture_source_identity_coverage']={'checkout_trace_ids':['a'*32],'paired_trace_ids':['a'*32]}
        t['generator_contract']={'checkout_request_ids':['request-1']}
        v['vm']['series'][0]['metric'].update(source='traces.servicegraph',failed='false')
        for key in ['network.protocol.name','http.response.status_code','rpc.grpc.status_code']:v['vm']['series'][0]['metric'].pop(key)
        self.assertEqual(p.prove('servicegraph.request.error.count',t,v)['state'],'pass')
        trace['paired_frontend']=False
        self.assertEqual(p.prove('servicegraph.request.error.count',t,v)['state'],'insufficient')

    def test_capture_lifetime_must_enclose_the_actual_counter_guard(self):
        t,v=self.fixture();t['structured_capture']['started_at']=11
        self.assertEqual(p.prove('servicegraph.request.count',t,v)['state'],'insufficient')
