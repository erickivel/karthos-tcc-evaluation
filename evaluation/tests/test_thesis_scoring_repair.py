import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from evaluation.consumers.api_only import thesis_answers
from evaluation.gke import consumer, thesis_history
from evaluation.gke.models import write
from evaluation.gke.thesis import claim_matrix, score_window_answers
from evaluation.timing_analysis import generator_contract

START='2026-10-01T00:00:00Z'
MID='2026-10-01T00:00:30Z'
END='2026-10-01T00:01:00Z'
SOURCE='mesh.istio'


class API:
    contract=True
    contract_errors=[]
    nodes=[
        {'kind':'service','id':'checkout-id','name':'checkout','namespace':'demo'},
        {'kind':'service','id':'frontend-id','name':'frontend','namespace':'demo'},
        {'kind':'service','id':'other-frontend','name':'frontend','namespace':'other'},
        {'kind':'pod','id':'frontend-pod','name':'frontend','namespace':'demo'},
    ]

    def __init__(self):
        self.history_queries=[]
        self.metric_queries=[]
        self.edges=[
            {'source':caller,'target':'checkout-id','relation':'calls',
             'provenance':[source],'first_seen':at,'last_seen':at}
            for caller,source,at in (
                ('frontend-id',SOURCE,MID),('other-frontend',SOURCE,MID),
                ('frontend-pod',SOURCE,MID),('frontend-id','traces.servicegraph',MID),
                ('frontend-id',SOURCE,'2026-10-01T00:01:30Z'))
        ]

    def observe(self,before,after):
        graph={'state':'observed','nodes':self.nodes,'edges':[]}
        return {'before':graph,'after':graph}

    def edge_history(self,before,after,source_id=None,**kwargs):
        self.history_queries.append((before,after,source_id,kwargs))
        return {'state':'observed','edges':self.edges,'requests':[]}

    def metrics(self,query):
        self.metric_queries.append(query)
        return {'state':'observed','samples':[]}


class ThesisScoringTests(unittest.TestCase):
    def test_confirmation_retains_actual_checkout_trace_ids_without_changing_verification(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)
            write(path/'verification.json',{'evidence':{'valid':True},'source':{'requests':2}})
            records=[
                {'run_id':'r','request_id':'1','route':'checkout','started_at':5,'trace_id':'sent'},
                {'run_id':'r','request_id':'2','route':'checkout_expired','started_at':301,
                 'trace_id':'fallback','requested_trace_id':'proposed','actual_trace_id':'actual'},
                {'run_id':'r','request_id':'3','route':'product','started_at':5,'trace_id':'unrelated'},
                {'run_id':'foreign','request_id':'4','route':'checkout','started_at':5,'trace_id':'foreign'},
            ]
            import json
            (path/'ledger.ndjson').write_text(''.join(json.dumps(row)+'\n' for row in records))
            result=generator_contract(path,{'run_id':'r','kind':'confirmation'},0,300)
        self.assertEqual(result['state'],'available')
        self.assertEqual(result['checkout_trace_ids'],['sent','actual'])
        self.assertEqual(result['checkout_request_ids'],['1','2'])

    def test_connector_graph_source_is_not_direct_otlp(self):
        from evaluation.consumers.api_only import graph_calls_source
        self.assertEqual(graph_calls_source('traces.servicegraph'), 'traces.servicegraph')
        self.assertEqual(graph_calls_source('traces.otlp'), 'traces.otlp')
        self.assertIsNone(graph_calls_source('unknown'))

    def test_consumer_queries_selected_source_and_uses_exact_scoped_service_ids(self):
        client=API()
        doc=thesis_answers(client,START,END,namespace='demo',service='checkout',source=SOURCE,container='server')
        self.assertEqual(client.history_queries[0][:3],(START,END,SOURCE))
        calls=doc['answers']['interval-callers']
        self.assertEqual(calls['state'],'observed')
        self.assertEqual([row['caller_id'] for row in calls['value']],['frontend-id'])
        errors=client.metric_queries[-1]
        self.assertEqual((errors['edge_source_id'],errors['edge_target_id']),('frontend-id','checkout-id'))

    def test_caller_scoring_requires_canonical_source_interval_and_independent_activity(self):
        client=API()
        doc=thesis_answers(client,START,END,namespace='demo',service='checkout',source=SOURCE,container='server')
        report={'manifest':{'namespace':'demo','source_id':SOURCE,'start':START,'end':END},
                'spec':{'demo':'astronomy'},'source_selection':{'state':'pass','expected_source':SOURCE},
                'traffic':{'report':{'recorder_identity_valid':True,'windows':{'whole':{
                    'observers':{'frontend':{'completion':{'requests':4}}},
                    'checkout_place_order':{'completion':{'requests':4}},
                    'raw_mesh_source':{'series':[{'metric':'istio_requests_total','increase':4,
                        'labels':{'reporter':'destination','source_workload_namespace':'demo',
                                  'destination_service_namespace':'demo','destination_service_name':'checkout',
                                  'source_workload':'frontend'}}]},
                }}}}}
        self.assertEqual(score_window_answers(doc,[],[],report)['interval-callers']['state'],'correct')
        doc['answers']['interval-callers']['value'][0]['caller_id']='other-frontend'
        self.assertEqual(score_window_answers(doc,[],[],report)['interval-callers']['state'],'incorrect')
        report['traffic']['report']['recorder_identity_valid']=False
        self.assertEqual(score_window_answers(doc,[],[],report)['interval-callers']['state'],'insufficient')

    def test_trace_comparison_requires_unique_linked_checkout_identities(self):
        from evaluation.gke.thesis_comparison import _observation
        report={'evidence':{'valid':True},'source_selection':{'state':'pass','expected_source':'traces.servicegraph'},
                'manifest':{'source_id':'traces.servicegraph'},'traffic':{'report':{
                    'recorder_identity_valid':True,'eventual_completeness':{'state':'available','requests':4},
                    'windows':{'whole':{'checkout_place_order':{'evidence_present':True,'completion':{'requests':4}}}},
                    'whole_capture_source_identity_coverage':{
                        'checkout_trace_ids':['a','b'],'paired_trace_ids':['a','b','priming']},
                    'generator_contract':{'checkout_request_ids':['r1','r2'],'conflicting_request_ids':[]}}}}
        self.assertEqual(_observation(report)['linked_checkout_trace_ids'],['a','b'])
        report['traffic']['report']['whole_capture_source_identity_coverage']['checkout_trace_ids']=['a','a']
        self.assertIsNone(_observation(report))

    def test_error_metadata_and_numeric_values_have_independent_verdicts(self):
        client=API()
        doc=thesis_answers(client,START,END,namespace='demo',service='checkout',source=SOURCE,container='server')
        labels={'source':SOURCE,'edge_client':'frontend','edge_server':'checkout',
                'edge_client_id':'frontend-id','edge_server_id':'checkout-id'}
        sample={'labels':labels,'entity_id':'frontend-id','metric_name':'servicegraph.request.error.count',
                'timestamp':END,'unit':'count','value':8,
                'derivation':{'id':'istio.request-errors','input_metric':'servicegraph.request.count',
                              'version':'1','interpretation':'HTTP or gRPC status policy'}}
        doc['answers']['request-error-derivation']={'state':'observed','value':{
            'samples':[sample],'query':{'aggregation':'increase','window':'1m0s','from':END,'to':END,'source':SOURCE}}}
        from evaluation.scoring import stamp
        report={'manifest':{'namespace':'demo','source_id':SOURCE,'start':START,'end':END},
                'spec':{'demo':'astronomy'},'source_selection':{'state':'pass','expected_source':SOURCE},
                'traffic':{'report':{'windows':{'whole':{'api_storage_parity':{'checks':{
                    'exact_error_count_api':{'checks':[{'labels':labels,'timestamp':stamp(END),'reference':3}]}
                }}}}}}}
        scored=score_window_answers(doc,[],[],report)
        self.assertEqual(scored['request-error-derivation']['state'],'correct')
        self.assertEqual(scored['request-error-numerical-agreement']['state'],'incorrect')
        sample['value']=3
        scored=score_window_answers(doc,[],[],report)
        self.assertEqual(scored['request-error-numerical-agreement']['state'],'insufficient')
        self.assertEqual(scored['request-error-numerical-agreement']['storage_api_parity']['state'],'correct')
        sample['timestamp']='2026-10-01T00:00:59.99999996Z'
        scored=score_window_answers(doc,[],[],report)
        self.assertEqual(scored['request-error-numerical-agreement']['storage_api_parity']['state'],'correct')
        sample['timestamp']='2026-10-01T00:00:59.999Z'
        scored=score_window_answers(doc,[],[],report)
        self.assertEqual(scored['request-error-numerical-agreement']['storage_api_parity']['state'],'insufficient')

    def test_trace_error_reference_requires_same_independently_observed_fixed_window(self):
        from evaluation.timing_analysis import fixed_window_error_reference
        trace_id='a'*32
        start,end=1,61
        manifest={'source_id':'traces.servicegraph','demo':'astronomy','namespace':'demo'}
        report={'recorder_identity_valid':True,'whole_capture_source_identity_coverage':{
                   'checkout_trace_ids':[trace_id],'paired_trace_ids':[trace_id]},
                'generator_contract':{'checkout_request_ids':['r1'],'conflicting_request_ids':[]}}
        observer={'boundary':'checkout','identity':'o','uri':'/checkout.PlaceOrder/PlaceOrder',
                  'start':20,'completion':21,'traceparent':'00-'+trace_id+'-'+'b'*16+'-01'}
        trace={'trace_id':trace_id,'paired_frontend':True,'conflicting_identity':False,
               'target_service':'checkout','start':20,'completion':21,'connector_failed':True}
        result=fixed_window_error_reference(manifest,report,[observer],[trace],start,end)
        self.assertEqual((result['state'],result['errors']),('available',1))
        observer['traceparent']='00-'+'c'*32+'-'+'b'*16+'-01'
        self.assertEqual(fixed_window_error_reference(manifest,report,[observer],[trace],start,end)['state'],'insufficient')

    def test_verified_invalid_window_and_history_cannot_pass_claims(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            rows=[]
            for i in range(6):
                path=root/str(i)
                write(path/'gke-evaluation.json',{
                    'spec':{'phase':'baseline'},'evidence':{'valid':i!=5},
                    'execution':{'state':'complete'},
                    'thesis_answers':{'interval-callers':{'state':'correct'}},
                })
                rows.append({'id':str(i),'path':str(path),'evidence_valid':i!=5,'execution':'complete',
                             'export':{'verified':True,'uri':'gs://evidence/'+str(i)}})
            ctx=SimpleNamespace(manifest={'matrix':[],'product_pins':{'karthos_revision':'test'}})
            history={'answer':{'state':'correct'},'continuation_safe':False,'export':{'verified':True,'uri':'gs://evidence/history'}}
            matrix=claim_matrix(ctx,rows,history,None)
        claims={row['claim']:row for row in matrix['claims']}
        self.assertEqual(claims['interval-callers']['verdict'],'insufficient')
        self.assertEqual(claims['retained-replicas-and-health']['verdict'],'insufficient')


class ThesisHistoryTests(unittest.TestCase):
    def test_incomplete_history_can_export_but_does_not_authorize_continuation(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            ctx=SimpleNamespace(result=root,deadline=time.time()+1000,manifest={},
                    config={'limits':{'cleanup_reserve_seconds':120}},
                    json=lambda *args: {},write=write,
                    state={'terraform':{'foundation':{'evidence_bucket':'bucket'}}},run_id='test')
            with patch.object(thesis_history.export,'upload',return_value={'verified':True}) as upload:
                report=thesis_history.capture(ctx,SimpleNamespace(namespace='demo'),{})
            self.assertTrue(upload.called)
            self.assertTrue(report['export']['verified'])
            self.assertFalse(report['continuation_safe'])
            self.assertFalse(hasattr(ctx,'phase_deadline'))

    def test_missing_newer_snapshot_with_stable_replacement_and_verified_export_can_continue(self):
        from contextlib import nullcontext
        old=[{'pod':'old','uid':'uid-old','node':'node-a','health':'healthy'}]
        new=[{'pod':'new','uid':'uid-new','node':'node-b','health':'healthy'}]
        pods=lambda rows:{'items':[{'metadata':{'uid':row['uid']}} for row in rows]}
        deployment={'spec':{'selector':{'matchLabels':{'app':'checkout'}}}}
        clock=[1000]
        def graph(*,at):
            if clock[0]==1000:
                return {'state':'observed','snapshot':{'id':1},'nodes':[
                    {'name':'old','kind':'pod','namespace':'demo'}]}
            return {'state':'insufficient','reason':'new snapshot has not materialized'}
        def advance(seconds):
            clock[0]+=seconds

        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            ctx=SimpleNamespace(result=root,deadline=time.time()+1000,manifest={},
                    config={'limits':{'cleanup_reserve_seconds':120}},
                    json=lambda verb,kind,*args: deployment if kind=='deployment' else pods(old),
                    kube=lambda *args,**kwargs: None,write=write,
                    state={'terraform':{'foundation':{'evidence_bucket':'bucket'}}},run_id='test')
            with patch.object(thesis_history.time,'monotonic',side_effect=lambda:clock[0]), \
                 patch.object(thesis_history.time,'sleep',side_effect=advance), \
                 patch.object(thesis_history.r,'forward',return_value=nullcontext('http://fixture')), \
                 patch.object(thesis_history,'APIConsumer',return_value=SimpleNamespace(graph=graph)), \
                 patch.object(thesis_history,'replica_inventory',side_effect=(old,new,new)), \
                 patch.object(thesis_history,'restart_target',return_value={'state':'requested'}), \
                 patch.object(thesis_history,'observe_restart',
                              side_effect=lambda *args,**kwargs: (advance(10),pods(new))), \
                 patch.object(thesis_history,'capture_consumer') as consumer, \
                 patch.object(thesis_history.export,'upload',return_value={'verified':True}):
                report=thesis_history.capture(ctx,SimpleNamespace(namespace='demo',source='mesh'),{})
            self.assertTrue(report['export']['verified'])
            self.assertTrue(report['continuation_safe'])
            self.assertEqual(report['answer']['state'],'insufficient')
            self.assertIn('newer retained',report['answer']['reason'])
            consumer.assert_not_called()

    def test_unvalidated_consumer_job_cannot_authorize_history(self):
        doc={'state':'observed','before':{'state':'observed'},'after':{'state':'observed'},
             'openapi_validated':False}
        self.assertFalse(consumer._job_observed(doc,contract=True))
        self.assertTrue(consumer._job_observed(doc,contract=False))
