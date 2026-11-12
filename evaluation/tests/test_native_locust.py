import io
import json
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace

try:
    from evaluation.workloads import native_locust
except ImportError:
    native_locust = None


class Signal:
    def __init__(self): self.listeners = []
    def add_listener(self, listener): self.listeners.append(listener)
    def fire(self, **kwargs):
        for listener in self.listeners: listener(**kwargs)


class NativeLocustTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(native_locust, 'native request adapter is not implemented')
        self.events = SimpleNamespace(test_start=Signal(), test_stop=Signal(), request=Signal())
        events = self.events
        self.calls = []
        calls = self.calls
        class Session:
            def request(self, method, url, *args, **kwargs):
                calls.append((method, url, args, kwargs))
                response = SimpleNamespace(status_code=503)
                events.request.fire(request_type=method, name=url, response_time=2.5,
                                    response=response, exception=None, context=kwargs['context'])
                return response
        self.session = Session

    def test_preserves_native_payload_and_logs_correlated_transport_result(self):
        output = io.StringIO()
        with redirect_stdout(output):
            native_locust.install(self.events, self.session, 'run-1', 180)
            self.events.test_start.fire(environment=None)
            result = self.session().request('POST', '/api/cart', json={'quantity':1}, headers={'X-Test':'value'})
            self.events.test_stop.fire(environment=None)
        lines = output.getvalue().splitlines()
        attempts = [json.loads(s.removeprefix('EVAL_ATTEMPT ')) for s in lines if s.startswith('EVAL_ATTEMPT ')]
        records = [json.loads(s.removeprefix('EVAL_REQUEST ')) for s in lines if s.startswith('EVAL_REQUEST ')]
        windows = [json.loads(s.removeprefix('EVAL_WINDOW ')) for s in lines if s.startswith('EVAL_WINDOW ')]
        self.assertEqual(len(attempts), 1)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['request_id'], attempts[0]['request_id'])
        self.assertEqual(records[0]['status'], 503)
        self.assertTrue(records[0]['native_reported'])
        self.assertIsNone(records[0]['outcome_ok'])
        self.assertEqual(records[0]['duration_ms'], 2.5)
        self.assertEqual(result.status_code, 503)
        self.assertEqual(self.calls[0][3]['json'], {'quantity':1})
        self.assertEqual(self.calls[0][3]['headers']['X-Test'], 'value')
        self.assertEqual(self.calls[0][3]['headers']['X-Evaluation-Request-ID'], records[0]['request_id'])
        self.assertIn('traceparent', self.calls[0][3]['headers'])
        self.assertNotIn('response_body', records[0])
        self.assertEqual(len(windows), 2)
        self.assertIsNone(windows[0]['ended_at'])
        self.assertIsNotNone(windows[1]['ended_at'])
        self.assertIsNone(windows[0]['expected_iterations'])

    def test_multiple_client_types_share_one_window_and_unique_request_ids(self):
        events = self.events
        class FastSession:
            def request(self, method, url, *args, **kwargs):
                response = SimpleNamespace(status_code=200)
                events.request.fire(response=response, response_time=1, context=kwargs['context'])
                return response
        output = io.StringIO()
        with redirect_stdout(output):
            native_locust.install(events, (self.session, FastSession), 'both', 10)
            events.test_start.fire()
            self.session().request('GET', '/')
            FastSession().request('GET', '/cart')
            events.test_stop.fire()
        rows = output.getvalue().splitlines()
        records = [json.loads(s.removeprefix('EVAL_REQUEST ')) for s in rows if s.startswith('EVAL_REQUEST ')]
        self.assertEqual(len(records), 2)
        self.assertEqual(len({r['request_id'] for r in records}), 2)
        self.assertEqual(sum(s.startswith('EVAL_WINDOW ') for s in rows), 2)
        self.assertEqual(len(events.request.listeners), 1)

    def test_terminal_uses_final_prepared_trace_header_not_requested_seed(self):
        events=self.events
        actual='abcdef0123456789abcdef0123456789'
        class Instrumented:
            base_url='http://frontend-proxy:8080'
            def request(self, method, url, **kwargs):
                headers=dict(kwargs['headers'])
                headers['traceparent']='00-'+actual+'-1234567890abcdef-03'
                response=SimpleNamespace(status_code=200,request=SimpleNamespace(headers=headers,url=self.base_url+url))
                events.request.fire(response=response,response_time=1,context=kwargs['context'])
                return response
        output=io.StringIO()
        with redirect_stdout(output):
            native_locust.install(events,Instrumented,'r',10)
            Instrumented().request('GET','/')
        record=[json.loads(x.removeprefix('EVAL_REQUEST ')) for x in output.getvalue().splitlines() if x.startswith('EVAL_REQUEST ')][0]
        self.assertEqual(record['trace_id'],actual)
        self.assertNotEqual(record['requested_trace_id'],actual)
        self.assertEqual(record['trace_context_evidence'],'response.request.headers')
        self.assertEqual(record['expected_boundary'],'frontend')

    def test_native_destination_rules_do_not_assume_every_request_hits_frontend(self):
        classify=native_locust.request_boundary
        self.assertEqual(classify('/api/cart','http://frontend-proxy:8080'),'frontend')
        self.assertEqual(classify('http://agent:8010/prompt','http://frontend-proxy:8080'),'agent')
        self.assertEqual(classify('http://agent.example:8010/prompt','http://frontend-proxy:8080'),'unknown')
        self.assertEqual(classify('http://agent:8011/prompt','http://frontend-proxy:8080'),'unknown')
        self.assertEqual(classify('http://agent:8010/unreviewed','http://frontend-proxy:8080'),'unknown')
        self.assertEqual(native_locust.route_name('POST','http://agent:8010/prompt'),'agent_prompt')

    def test_transport_error_records_terminal_and_preserves_exception(self):
        class Broken:
            def request(self, *args, **kwargs): raise OSError('connection refused')
        output = io.StringIO()
        with redirect_stdout(output):
            native_locust.install(self.events, Broken, 'run-1', 180)
            with self.assertRaisesRegex(OSError, 'connection refused'):
                Broken().request('GET', '/')
        terminal = [json.loads(s.removeprefix('EVAL_REQUEST ')) for s in output.getvalue().splitlines() if s.startswith('EVAL_REQUEST ')][0]
        self.assertEqual(terminal['status'], 0)
        self.assertFalse(terminal['completed'])
        self.assertFalse(terminal['native_reported'])
        self.assertIn('connection refused', terminal['exception'])

    def test_catch_response_can_finish_after_request_returns(self):
        events = self.events
        class Deferred:
            def request(self, method, url, **kwargs):
                return lambda: events.request.fire(request_type=method,name=url,response_time=7,
                    response=SimpleNamespace(status_code=200),exception=None,context=kwargs['context'])
        output = io.StringIO()
        with redirect_stdout(output):
            native_locust.install(events, Deferred, 'r', 180)
            finish = Deferred().request('GET','/product/secret-id',catch_response=True)
            self.assertNotIn('EVAL_REQUEST ',output.getvalue())
            finish()
        records=[json.loads(s.removeprefix('EVAL_REQUEST ')) for s in output.getvalue().splitlines() if s.startswith('EVAL_REQUEST ')]
        self.assertEqual(len(records),1)
        self.assertEqual(records[0]['route'],'product')

    def test_native_manifest_selects_observer_without_replacing_upstream_tasks(self):
        from evaluation.manifests import locust_job
        job=locust_job('astronomy','r','ns','image','http://target',artifact_mode=True,native_ledger=True)
        container=job['spec']['template']['spec']['containers'][0]
        self.assertIn('native_locust.py',container['args'][0])
        self.assertNotIn('-f /scripts/matched_locust.py',container['args'][0])
        self.assertEqual(container['volumeMounts'][0]['mountPath'],'/scripts')
        self.assertIn('--users 5 --spawn-rate 1',container['args'][0])

    def test_bounded_routes_do_not_leak_dynamic_paths_or_queries(self):
        self.assertEqual(native_locust.route_name('GET','https://shop/product/private?userId=abc'),'product')
        self.assertEqual(native_locust.route_name('GET','/unknown/private/value?token=a'),'other')
        self.assertEqual(native_locust.route_name('POST','/api/checkout?currencyCode=USD'),'checkout')


if __name__ == '__main__': unittest.main()
