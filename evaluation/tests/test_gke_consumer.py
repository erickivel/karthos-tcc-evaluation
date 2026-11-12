import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
try:
    from evaluation.consumers.api_only import APIConsumer, answer_questions
except ImportError:
    APIConsumer = answer_questions = None

class ConsumerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(APIConsumer,'standalone API consumer missing')
        self.requests=[];self.drift=False
        outer=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                q=parse_qs(urlparse(self.path).query);outer.requests.append((self.path,q))
                page=q.get('next_token')==['next']
                snapshot={'id':2 if outer.drift and page else 1,'created_at':'2026-10-01T00:00:00Z'}
                if '/metrics' in self.path: body={'data':{'samples':[{'metric_name':'phase','value':2}],'total':1}}
                elif '/nodes' in self.path:
                    body={'data':{'snapshot':snapshot,'nodes':[{'id':'p' if page else 'checkout','kind':'pod' if page else 'service','name':'checkout','labels':{}}],'total':2}}
                    if not page: body['next_token']='next'
                else: body={'data':{'snapshot':snapshot,'edges':[{'source':'checkout','target':'p','relation':'calls'}],'total':1}}
                self.send_response(200);self.end_headers();self.wfile.write(json.dumps(body).encode())
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.client=APIConsumer('http://127.0.0.1:'+str(self.server.server_port))
    def tearDown(self):
        if hasattr(self,'server'): self.server.shutdown();self.server.server_close();self.thread.join()
    def test_graph_pages_are_pinned_and_snapshot_drift_is_insufficient(self):
        graph=self.client.graph(at='2026-10-01T00:01:00Z')
        self.assertEqual(graph['state'],'observed');self.assertEqual(len(graph['nodes']),2)
        self.assertTrue(all(q.get('at') for path,q in self.requests))
        self.drift=True
        self.assertEqual(self.client.graph(at='2026-10-01T00:01:00Z')['state'],'insufficient')
    def test_metric_source_filter_and_integer_status_preserved(self):
        result=self.client.metrics({'metric_name':'phase','source':'kubelet'})
        self.assertEqual(result['samples'][0]['value'],2)
        self.assertEqual(self.requests[0][1]['source'],['kubelet'])
    def test_before_after_and_questions_do_not_guess_missing_placements(self):
        observations=self.client.observe('2026-10-01T00:01:00Z','2026-10-01T00:02:00Z',queries=[{'metric_name':'cpu'}])
        self.assertFalse(observations['snapshot_changed'])
        answers=answer_questions(observations['after'],observations['metrics'],service='checkout')
        self.assertEqual(answers['node_placements']['state'],'insufficient')
        self.assertIn('observed',answers['replicas'])
    def test_endpoint_rejects_credentials_and_non_http(self):
        for endpoint in ('file:///proc','http://user:secret@localhost','http://localhost/api?token=x'):
            with self.assertRaises(ValueError): APIConsumer(endpoint)
    def test_answers_use_only_explicit_calls_ownership_and_placement(self):
        graph={'state':'observed','nodes':[{'id':'s','kind':'service','name':'checkout'},{'id':'w','kind':'workload'},{'id':'p','kind':'pod'},{'id':'n','kind':'node'}],
               'edges':[{'source':'caller','target':'s','relation':'calls'}, {'source':'s','target':'w','relation':'exposes'}, {'source':'w','target':'p','relation':'contains'}, {'source':'p','target':'n','relation':'runs_on'}]}
        answers=answer_questions(graph,[{'state':'observed','samples':[{'value':2,'value_label':'Running','timestamp':'original'}]}])
        self.assertEqual(answers['callers']['observed'],[{'source':'caller','target':'s'}])
        self.assertEqual(answers['replicas']['observed'],1)
        self.assertEqual(answers['node_placements']['observed'],[{'pod':'p','node':'n'}])
        self.assertEqual(answers['resource_traffic']['observed'][0]['value_label'],'Running')
        self.assertEqual(answers['resource_traffic']['observed'][0]['timestamp'],'original')
