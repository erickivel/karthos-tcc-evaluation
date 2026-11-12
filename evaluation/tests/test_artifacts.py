import json
import tempfile
import unittest
from pathlib import Path

from evaluation.artifacts import extract_marked, evaluate_bundle, score_count


class ArtifactTests(unittest.TestCase):
    def test_extract_marked_k6_log(self):
        raw = 'starting\n__EVAL_K6_BEGIN__\n{"type":"Point"}\n__EVAL_K6_END__\n'
        self.assertEqual(extract_marked(raw, 'K6'), '{"type":"Point"}\n')
        with self.assertRaises(ValueError):
            extract_marked('missing markers', 'K6')

    def test_count_tolerance_and_missing(self):
        self.assertEqual(score_count(100, 95, 0.10)['state'], 'pass')
        self.assertEqual(score_count(100, 70, 0.10)['state'], 'fail')
        self.assertEqual(score_count(100, None, 0.10)['state'], 'missing')

    def test_bundle_checks_actual_requests_and_provenance(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            (path / 'k6.ndjson').write_text(json.dumps({'type':'Point','metric':'http_reqs','data':{'time':'2026-10-18T12:00:01Z','value':1,'tags':{'route':'checkout','status':'200'}}})+'\n')
            (path / 'api-request-count.json').write_text(json.dumps({'data':{'samples':[{'value':1,'labels':{'source':'mesh.istio','edge_client':'frontend','edge_server':'checkoutservice'}}]}}))
            (path / 'manifest.json').write_text(json.dumps({'source':'trace','start':'2026-10-18T12:00:00Z','end':'2026-10-18T12:01:00Z'}))
            result=evaluate_bundle(path, {'request_relative_tolerance':0.1})
            self.assertEqual(result['provenance']['wrong'],1)
            self.assertEqual(result['requests']['actual_completed'],1)
            self.assertEqual(result['requests']['comparison']['state'],'pass')

    def test_sparse_checkout_counter_uses_raw_delta_with_boundary_coverage(self):
        from evaluation.artifacts import _checkout_raw_delta
        samples=[{'timestamp':f'2026-10-18T12:00:{sec:02d}Z','value':value,
                  'labels':{'edge_client':'frontend','edge_server':'checkoutservice','source':'traces.servicegraph'}}
                 for sec,value in [(15,2),(30,4),(45,7),(59,9)]]
        result=_checkout_raw_delta(samples,'boutique','2026-10-18T12:00:00Z','2026-10-18T12:01:00Z')
        self.assertEqual(result['value'],7)
        self.assertEqual(result['method'],'raw_counter_delta')
        late=samples[2:]
        self.assertIsNone(_checkout_raw_delta(late,'boutique','2026-10-18T12:00:00Z','2026-10-18T12:01:00Z')['value'])


if __name__ == '__main__':
    unittest.main()

class GraphTests(unittest.TestCase):
    def test_entity_sets_detect_missing_pod_and_service(self):
        from evaluation.artifacts import evaluate_entities
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)
            (path/'manifest.json').write_text(json.dumps({'demo':'boutique'}))
            (path/'k8s-pods.json').write_text(json.dumps({'items':[{'metadata':{'name':'pod-a','namespace':'demo'}}]}))
            (path/'k8s-services.json').write_text(json.dumps({'items':[{'metadata':{'name':'svc-a','namespace':'demo'}}]}))
            (path/'api-nodes.json').write_text(json.dumps({'data':{'nodes':[{'kind':'service','name':'svc-a','namespace':'demo'}]}}))
            result=evaluate_entities(path,'demo')
            self.assertEqual(result['missing'],[('pod','demo','pod-a')])

class EdgeTests(unittest.TestCase):
    def test_pod_runs_on_edge_is_compared_to_kubernetes_assignment(self):
        from evaluation.artifacts import evaluate_runs_on
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)
            (path/'k8s-pods.json').write_text(json.dumps({'items':[{'metadata':{'name':'pod-a','namespace':'demo'},'spec':{'nodeName':'node-a'}}]}))
            (path/'api-nodes.json').write_text(json.dumps({'data':{'nodes':[{'id':'p1','kind':'pod','name':'pod-a','namespace':'demo'}, {'id':'n1','kind':'node','name':'node-a'}]}}))
            (path/'api-edges.json').write_text(json.dumps({'data':{'edges':[]}}))
            result=evaluate_runs_on(path,'demo')
            self.assertEqual(result['missing'],[('p1','n1','runs_on')])

class ResourceTests(unittest.TestCase):
    def test_resource_coverage_reports_absent_and_wrong_source(self):
        from evaluation.artifacts import resource_coverage
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)
            (path/'api-cpu.json').write_text(json.dumps({'data':{'samples':[{'timestamp':'2026-10-18T12:00:00Z','entity_id':'c1','labels':{'source':'mesh.istio'}}]}}))
            result=resource_coverage(path,'2026-10-18T12:00:10Z')
            self.assertEqual(result['cpu']['source']['wrong'],1)
            self.assertEqual(result['memory']['state'],'missing')
