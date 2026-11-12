"""Bounded poll receipts remain available when readiness evidence is empty."""
import contextlib
import io
import json
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from evaluation.gke import poll_capture as p
from evaluation.gke.models import read


class PollCaptureTests(unittest.TestCase):
    def test_transport_caps_timeout_and_bytes_before_decode(self):
        stream = io.BytesIO(b'x'*10000)
        with patch.object(p.urllib.request, 'urlopen', return_value=stream) as open_url:
            with self.assertRaisesRegex(ValueError, 'byte bound'):
                p.bounded_json('http://fixture', time.monotonic()+.5, 100)
        self.assertLessEqual(open_url.call_args.kwargs['timeout'], .5)
        self.assertTrue(stream.closed)

    def test_expired_deadline_makes_no_network_request(self):
        with patch.object(p.urllib.request, 'urlopen') as open_url:
            with self.assertRaises(TimeoutError):
                p.bounded_json('http://fixture', time.monotonic()-1, 100)
        open_url.assert_not_called()

    def test_http_error_body_is_retained_and_bounded(self):
        error = urllib.error.HTTPError('http://fixture', 503, 'unavailable', {}, io.BytesIO(b'{"error":"not-ready"}'))
        with patch.object(p.urllib.request, 'urlopen', side_effect=error):
            with self.assertRaisesRegex(RuntimeError, 'HTTP 503.*not-ready'):
                p.bounded_json('http://fixture', time.monotonic()+1, 100)

    def test_empty_pages_and_error_are_saved_before_storage_failure(self):
        pages = [{'data':{'samples':[]}, 'next_token':'second'}, RuntimeError('HTTP 503: waiting')]
        with tempfile.TemporaryDirectory() as tmp:
            ctx = SimpleNamespace(root=tmp)
            with patch.object(p, 'bounded_json', side_effect=pages), patch('evaluation.runner.kube', side_effect=ValueError('no VM')):
                payload, receipt = p.capture(ctx, Path(tmp), 'http://fixture', {'metric_name':'test'}, time.monotonic()+2)
            self.assertIsNone(payload)
            self.assertEqual(read(Path(tmp)/'page-0000.json')['response'], pages[0])
            self.assertIn('HTTP 503', read(Path(tmp)/'page-0001.json')['error'])
            self.assertIn('api_error', receipt)
            self.assertIn('storage_error', read(Path(tmp)/'receipt.json'))

    def test_measured_empty_poll_retains_equivalent_storage_and_reuses_fixed_target(self):
        from evaluation.timing_capture import AvailabilityCapture
        from evaluation.protocol import Cell
        from evaluation.sample_records import iter_records
        with tempfile.TemporaryDirectory() as tmp:
            capture = AvailabilityCapture(Cell('boutique', 'trace', 1), Path(tmp), SimpleNamespace(pods={}))
            capture.api, capture.vm = 'http://api', 'http://vm'
            compiled = {'expression':'exact-expression'}
            document = {'request':{'query':'exact-expression'}, 'response':{'status':'success','data':{'result':[]}}}
            with patch.object(capture, '_api_pages', return_value={'data':{'samples':[]}}), patch.object(p, 'compiled_storage', return_value=(compiled, document)) as storage:
                capture._api('servicegraph.request.count')
                capture._api('servicegraph.request.count')
            self.assertIsNone(storage.call_args_list[0].args[-1])
            self.assertEqual(storage.call_args_list[1].args[-1], compiled)
            self.assertEqual(storage.call_args_list[0].args[0], storage.call_args_list[1].args[0])
            rows = list(iter_records(capture.writer.path))
            self.assertEqual(sum(row['source']=='api-storage' for row in rows), 2)
            self.assertEqual(sum(row['source']=='api' and row['response']['data']['samples']==[] for row in rows), 2)

    def test_storage_transport_matches_product_single_grid_and_offgrid_semantics(self):
        # An instant query must never fill an empty single-point range response.
        cases = [
            (['2026-11-10T00:00:00Z'], ['/api/v1/query_range']),
            (['2026-11-10T00:00:00Z','2026-11-10T00:00:15Z'], ['/api/v1/query_range']),
            (['2026-11-10T00:00:00Z','2026-11-10T00:00:15Z','2026-11-10T00:00:20Z'],
             ['/api/v1/query_range','/api/v1/query']),
        ]
        for points, expected in cases:
            with self.subTest(points=points):
                compiled = {'expression':'production-expression', 'from':points[0], 'to':points[-1],
                            'step_seconds':15, 'evaluation_points':points}
                calls = []
                def fetch(route, request):
                    calls.append((route, request))
                    return {'status':'success', 'data':{'result':[] if route.endswith('query_range') else [{'value':[1,'9']}]}}
                _, document = p.compiled_storage({}, '.', fetch, time.monotonic()+1, compiled)
                self.assertEqual([route for route, _ in calls], expected)
                self.assertEqual(document['response']['data']['result'], [])
                self.assertEqual('final' in document, len(expected)==2)
                if len(expected)==2:
                    self.assertEqual(calls[-1][1]['time'], points[-1])

    def test_compiler_binary_is_built_once_for_two_exact_evaluations(self):
        compiled = {'expression':'production-expression', 'from':'2026-11-10T00:00:00Z',
                    'to':'2026-11-10T00:00:00Z', 'step_seconds':15,
                    'evaluation_points':['2026-11-10T00:00:00Z']}
        calls = []
        def command(args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace(stdout='' if args[0]=='go' else json.dumps(compiled))
        fetches = []
        def fetch(route, request):
            fetches.append((route, request))
            return {'status':'success', 'data':{'result':[]}}
        with tempfile.TemporaryDirectory() as tmp, patch('evaluation.runner.cmd', side_effect=command):
            for at in ('first', 'second'):
                p.compiled_storage({'from':at}, tmp, fetch, time.monotonic()+2)
        self.assertEqual(sum(args[0]=='go' for args, _ in calls), 1)
        self.assertEqual([json.loads(opts['input_text']) for args, opts in calls if args[0]!='go'], [{'from':'first'}, {'from':'second'}])
        self.assertEqual([route for route, _ in fetches], ['/api/v1/query_range']*2)
        self.assertTrue(all(q['query']=='production-expression' for _, q in fetches))
