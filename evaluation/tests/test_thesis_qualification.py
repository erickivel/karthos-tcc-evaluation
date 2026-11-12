import unittest
from pathlib import Path

from evaluation.consumers.api_only import answer_questions
from evaluation.gke import models
from evaluation.gke.evaluate import catalog_coverage

ROOT = Path(__file__).resolve().parents[2]


class ThesisQualificationTests(unittest.TestCase):
    def test_exact_schedule_and_legacy_matrices(self):
        old = models.read(ROOT/'protocols/gke-qualification-v1/protocol.yaml')
        self.assertEqual(len(models.build_matrix(old)), 46)
        self.assertEqual(len(models.build_matrix(old, schedule=models.REHEARSAL)), 16)
        legacy = models.read(ROOT/'protocols'/models.THESIS_V2/'protocol.yaml')
        self.assertEqual(len(models.build_matrix(legacy, schedule=models.THESIS_V2)), 12)
        definition = models.read(ROOT/'protocols'/models.THESIS/'protocol.yaml')
        rows = models.build_matrix(definition, schedule=models.THESIS)
        self.assertEqual(len(rows), 12)
        self.assertEqual(sum(row['duration_seconds'] for row in rows), 2160)
        self.assertEqual([(row['repeat'],row['cell']) for row in rows[::2]], [
            (1,'boutique-trace'),(1,'astronomy-trace'),(1,'astronomy-mesh'),
            (1,'boutique-mesh'),(2,'boutique-mesh'),(2,'boutique-trace')])
        for smoke, baseline in zip(rows[::2],rows[1::2]):
            self.assertEqual((smoke['phase'],baseline['phase']),('smoke','baseline'))
            self.assertEqual(smoke['cell'],baseline['cell'])
            self.assertEqual(smoke['generator'],baseline['generator'])
            self.assertEqual(smoke['generator'],'k6')

    def test_current_topology_traversal_is_namespace_scoped(self):
        def node(id, kind, name, namespace='app'):
            return dict(id=id,kind=kind,name=name,namespace=namespace)
        nodes = [node('s','service','checkout'),node('other','service','checkout','other'),
                 node('d','workload','deployment'),node('r','workload','replicaset'),
                 node('p','pod','checkout-1'),node('n','node','node-1','')]
        edges = [dict(source=a,target=b,relation=c) for a,b,c in
                 [('s','d','exposes'),('d','r','contains'),('r','p','contains'),('p','n','runs_on')]]
        graph = dict(state='observed',nodes=nodes,edges=edges)
        result = answer_questions(graph,[],service='checkout',namespace='app')
        self.assertEqual(result['replicas']['observed'],1)
        self.assertEqual(result['node_placements']['observed'],[{'pod':'p','node':'n'}])
        self.assertEqual(answer_questions(graph,[],service='checkout')['replicas']['state'],'insufficient')

    def test_mesh_error_coverage_uses_request_input_and_known_policy(self):
        name='servicegraph.request.error.count'
        input_name='servicegraph.request.count'
        labels={'source':'mesh.istio','edge_id':'e','network.protocol.name':'grpc','rpc.grpc.status_code':'13'}
        sample=dict(entity_id='c',edge_id='e',labels=labels,timestamp=100,unit='per_second',
                    derivation={'id':'istio.request-errors','version':'1','input_metric':input_name})
        row={'metric':dict(labels,__name__=input_name.replace('.','_'),entity_id='c'),
             'timestamps':[100000],'values':[7]}
        definition={'entries':[{'name':name}],'expected_count':1,'contract':'thesis-qualification-v2'}
        args=(definition,{'data':{'metrics':[{'name':name}]}},{'metric_assignments':{name:'mesh.istio'}},
              {name:{'data':{'samples':[sample]}}},[row],99,101,{})
        self.assertEqual(catalog_coverage(*args)['entries'][0]['classification'],'observed')
        labels.pop('rpc.grpc.status_code')
        self.assertEqual(catalog_coverage(*args)['entries'][0]['classification'],'missing-expected')

    def test_missing_graph_returns_insufficient_answers(self):
        from types import SimpleNamespace
        from evaluation.consumers.api_only import thesis_answers
        missing={'state':'insufficient','reason':'snapshot expired'}
        client=SimpleNamespace(observe=lambda *args:{'before':missing,'after':missing},
                               edge_history=lambda *args:missing,contract={'responses':{}},contract_errors=[])
        result=thesis_answers(client,'before','after',namespace='app',service='checkout',
                              source='mesh.istio',container='server')
        self.assertEqual(result['answers']['after_replicas']['state'],'insufficient')

    def test_history_requires_correct_health_and_a_distinct_snapshot(self):
        from evaluation.gke.thesis import score_history
        old=[{'pod':'old','node':'a','health':'healthy'}]
        new=[{'pod':'new','node':'b','health':'healthy'}]
        doc={'openapi_validated':True,'snapshot_changed':True,'answers':{
            'before_replicas':{'state':'observed','value':old},
            'after_replicas':{'state':'observed','value':new}}}
        self.assertEqual(score_history(doc,old,new)['state'],'correct')
        doc['answers']['after_replicas']['value']=[dict(new[0],health='failed')]
        self.assertEqual(score_history(doc,old,new)['state'],'incorrect')
        doc['snapshot_changed']=False
        self.assertEqual(score_history(doc,old,new)['state'],'insufficient')

    def test_live_contract_receipt_cannot_pass_without_all_evidence(self):
        from evaluation.gke.thesis import local_contract,START_REVISION
        from evaluation.diagnostics.thesis_local import qualify
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory:
            pins={'karthos_revision':START_REVISION,'karthos_source_hash':'a'*64}
            receipt=qualify(directory,pins)
            self.assertTrue(all(row['state']=='insufficient' for row in receipt['checks'].values()))
            with self.assertRaisesRegex(ValueError,'local thesis contract'):
                local_contract(receipt,pins,directory=directory)

    def test_history_runs_after_seal_and_before_rebuild(self):
        from tempfile import TemporaryDirectory
        from types import SimpleNamespace
        from unittest.mock import Mock
        import time
        from evaluation.gke.run import execute_matrix
        from evaluation.gke.thesis import START_REVISION
        from test_gke_run import FakeDriver
        protocol=models.read(ROOT/'protocols'/models.THESIS/'protocol.yaml')
        matrix=models.build_matrix(protocol,schedule=models.THESIS)
        with TemporaryDirectory() as directory:
            ctx=SimpleNamespace(result=Path(directory),deadline=time.time()+3600,event=Mock(),
                 write=models.write,config={'limits':{'cleanup_reserve_seconds':1800}},
                 manifest={'schedule':models.THESIS,'protocol_definition':protocol,'matrix':matrix,
                           'product_pins':{'karthos_revision':START_REVISION},'readiness_check':{'required':True}})
            driver=FakeDriver()
            def history(spec):
                driver.events.append(('history',spec['id']))
                return {'export':{'verified':True,'uri':'gs://fixture/history'},'answer':{'state':'insufficient'},
                        'continuation_safe':True}
            driver.thesis_history=history
            report=execute_matrix(ctx,driver,gate=lambda *_:{'ready':True})
            self.assertEqual(report['state'],'complete')
            self.assertEqual(report['executed_windows'],12)
            self.assertFalse(report['qualified'])
            expected=('history',matrix[7]['id'])
            at=driver.events.index(expected)
            self.assertEqual(driver.events[at-1],('upload',matrix[7]['id']))
            self.assertEqual(driver.events[at+1],('repeat',2))
            self.assertTrue((ctx.result/'claim-matrix.json').is_file())

    def test_wrong_api_resource_value_is_incorrect_with_exact_identity(self):
        from evaluation.gke.thesis import score_window_answers
        identity=['app','checkout-pod','server']
        response={'samples':[{'metric_name':'container.cpu.usage','timestamp':100,'value':0.5}]}
        document={'openapi_validated':True,'answers':{'container-resource-history':
                  {'state':'observed','container':identity,'value':[response]}}}
        report={'resources':{'checks':[{'container':identity,'metric':'cpu',
                'evaluation_timestamp_ms':100000,'preservation':{'api':0.25},
                'state':'pass','independent_agreement':{'state':'pass','reference':0.25}}]}}
        result=score_window_answers(document,None,None,report)
        self.assertEqual(result['container-resource-history']['state'],'incorrect')
