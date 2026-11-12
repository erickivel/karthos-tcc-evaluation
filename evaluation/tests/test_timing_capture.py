import base64
import json
import contextlib
import gzip
import hashlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from evaluation.timing_capture import AvailabilityCapture, CLOCK_PEER, clock_samples, clock_stream
from evaluation.protocol import Cell
from evaluation.sample_records import iter_records


def pod():
    return {'metadata': {'name': 'agent-a', 'uid': 'uid-a'}, 'spec': {'nodeName': 'node-a'}, 'status': {'containerStatuses': [{'name': 'agent', 'restartCount': 0}]}}


def reader_result(text):
    if text == 'missing\n':
        return SimpleNamespace(stdout=b'missing\n', stderr=b'', returncode=0)
    header, encoded = text.split('\n', 1)
    raw = base64.b64decode(encoded)
    framed=(header+':'+hashlib.sha256(raw).hexdigest()+'\n'+encoded).encode()
    return SimpleNamespace(stdout=framed, stderr=b'', returncode=0)


class TimingCaptureTests(unittest.TestCase):
    def setUp(self):
        renderer = patch('evaluation.gke.poll_capture.compiled_storage', side_effect=ValueError('fixture has no product renderer'))
        renderer.start()
        self.addCleanup(renderer.stop)

    def capture(self, directory):
        return AvailabilityCapture(Cell('boutique', 'trace', 1), Path(directory), SimpleNamespace(pods={'agent': pod()}))

    @staticmethod
    def framed(raw, stderr=b'', digest=None):
        digest = digest or hashlib.sha256(raw).hexdigest()
        stdout = f'3:{len(raw)}:{digest}\n'.encode()+base64.b64encode(raw)+b'\n'
        return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=0)

    def test_binary_transport_keeps_stderr_out_of_valid_trace_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = self.capture(directory)
            with patch('evaluation.timing_capture.r.kube', return_value=self.framed(b'{"a":1}\n', stderr=b'warning: \xfd')) as kube:
                capture._tail('agent', pod(), 'metrics.json')
            rows = list(iter_records(capture.writer.path))
            self.assertEqual(rows[-1]['body'], '{"a":1}\n')
            self.assertTrue(kube.call_args.kwargs['binary'])
            self.assertEqual(base64.b64decode(rows[-1]['transport_stderr_base64']), b'warning: \xfd')
            self.assertEqual(capture.status['errors'], [])

    def test_multinode_reader_transfers_large_chunk_with_original_hash_and_offsets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repair = SimpleNamespace(pods={'agent:node-a': pod()})
            capture = AvailabilityCapture(Cell('boutique', 'trace', 1), root, repair)
            raw = b'{"metric":"fixture","values":[1,2,3]}\n' * 60000
            (root/'metrics.json').write_bytes(raw)
            transfers = []
            def read(*args, **kwargs):
                script = args[-1].replace('/diag/', str(root)+'/')
                if sys.platform == 'darwin':
                    script = script.replace("stat -c '%i:%s'", "stat -f '%i:%z'")
                result = subprocess.run(['sh', '-c', script], capture_output=True, check=True,
                                        timeout=kwargs['timeout'])
                transfers.append(len(result.stdout))
                return result
            with patch('evaluation.timing_capture.r.kube', side_effect=read):
                capture._tail('agent:node-a', pod(), 'metrics.json')
                capture._tail('agent:node-a', pod(), 'metrics.json')
            first, second = iter_records(capture.writer.path)
            self.assertLess(transfers[0], len(raw)//10)
            self.assertEqual(first['chunk_sha256'], hashlib.sha256(raw).hexdigest())
            self.assertEqual(base64.b64decode(first['raw_chunk_base64']), raw)
            self.assertEqual(first['end_offset'], len(raw))
            self.assertEqual(second['offset'], len(raw))
            self.assertEqual(second['backlog_bytes'], 0)

    def test_multinode_rejects_gzip_frame_larger_than_declared_source_range(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = AvailabilityCapture(Cell('boutique', 'trace', 1), Path(directory),
                                          SimpleNamespace(pods={'agent:node-a': pod()}))
            raw = b'{}\n'
            frame = f'3:1:{hashlib.sha256(raw).hexdigest()}\n'.encode()+base64.b64encode(gzip.compress(raw))
            response = SimpleNamespace(stdout=frame, stderr=b'', returncode=0)
            with patch('evaluation.timing_capture.r.kube', return_value=response):
                with self.assertRaisesRegex(ValueError, 'compressed collector frame'):
                    capture._tail('agent:node-a', pod(), 'metrics.json')
            self.assertEqual(capture._files['agent:node-a:metrics.json']['offset'], 0)

    def test_corrupt_transfer_retries_same_offset_and_retains_bad_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = self.capture(directory)
            bad=self.framed(b'{"a":2}\n', digest=hashlib.sha256(b'{"a":1}\n').hexdigest())
            with patch('evaluation.timing_capture.r.kube', side_effect=[bad,self.framed(b'{"a":1}\n')]) as kube:
                capture._tail('agent', pod(), 'metrics.json')
            self.assertEqual(kube.call_count, 2)
            self.assertEqual(kube.call_args_list[0].args, kube.call_args_list[1].args)
            rows=list(iter_records(capture.writer.path))
            self.assertIn('checksum', rows[0]['attempt_error'])
            self.assertEqual(base64.b64decode(rows[0]['stdout_base64']), bad.stdout)
            self.assertEqual(rows[-1]['body'], '{"a":1}\n')
            self.assertEqual(capture.status['transport_recoveries'], 1)

    def test_repeated_bad_wire_bytes_remain_failure_and_do_not_advance(self):
        with tempfile.TemporaryDirectory() as directory:
            capture=self.capture(directory)
            bad=SimpleNamespace(stdout=b'3:10:\xfd\n',stderr=b'',returncode=0)
            with patch('evaluation.timing_capture.r.kube',return_value=bad) as kube:
                with self.assertRaises(UnicodeError):
                    capture._tail('agent',pod(),'metrics.json')
            self.assertEqual(kube.call_count,3)
            self.assertEqual(capture._files['agent:metrics.json']['offset'],0)
            rows=list(iter_records(capture.writer.path))
            self.assertEqual(len(rows),3)
            self.assertTrue(all(base64.b64decode(x['stdout_base64'])==bad.stdout for x in rows))

    def test_invalid_source_utf8_is_retained_and_not_retried_or_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = self.capture(directory)
            raw=b'{"a":"\xfd"}\n'
            with patch('evaluation.timing_capture.r.kube', return_value=self.framed(raw)) as kube:
                with self.assertRaises(UnicodeError):
                    capture._tail('agent', pod(), 'metrics.json')
            self.assertEqual(kube.call_count, 1)
            self.assertEqual(capture._files['agent:metrics.json']['offset'], 0)
            rows=list(iter_records(capture.writer.path))
            self.assertEqual(base64.b64decode(rows[-1]['raw_chunk_base64']), raw)

    def test_tail_preserves_split_lines_and_offsets(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = self.capture(directory)
            chunks = [b'{"a":', b'1}\n']
            outputs = [reader_result(f'3:{sum(map(len,chunks[:i+1]))}\n'+base64.b64encode(chunk).decode()) for i, chunk in enumerate(chunks)]
            with patch('evaluation.timing_capture.r.kube', side_effect=outputs):
                capture._tail('agent', pod(), 'metrics.json')
                capture._tail('agent', pod(), 'metrics.json')
            rows = list(iter_records(capture.writer.path))
            self.assertEqual(rows[0]['body'], '')
            self.assertEqual(rows[1]['body'], '{"a":1}\n')
            self.assertEqual(rows[1]['offset'], 5)
            self.assertEqual(rows[1]['backlog_bytes'], 0)

    def test_rotation_and_truncation_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = self.capture(directory)
            with patch('evaluation.timing_capture.r.kube', side_effect=[reader_result('3:2\ne30='), reader_result('4:0\n')]):
                capture._tail('agent', pod(), 'metrics.json')
                with self.assertRaisesRegex(ValueError, 'rotation'):
                    capture._tail('agent', pod(), 'metrics.json')

    def test_empty_capture_is_invalid_and_status_atomic(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = self.capture(directory)
            result = capture.stop()
            self.assertFalse(result['valid'])
            self.assertTrue(result['errors'])
            self.assertFalse((Path(directory)/'availability-status.json.tmp').exists())

    def test_not_yet_created_file_is_negative_observation_until_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = self.capture(directory)
            with patch('evaluation.timing_capture.r.kube', return_value=reader_result('missing\n')):
                capture._tail('agent', pod(), 'metrics.json')
            self.assertEqual(capture.status['errors'], [])
            self.assertFalse(list(iter_records(capture.writer.path))[0]['exists'])
            self.assertFalse(capture.stop()['valid'])

    def test_vm_exports_raw_ndjson_and_source_partition(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = self.capture(directory)
            capture.vm = 'http://fixture.invalid'
            series = {'metric': {'__name__': 'servicegraph_request_count'}, 'timestamps': [10], 'values': [3]}
            with patch('evaluation.timing_capture.urllib.request.urlopen', return_value=io.BytesIO((json.dumps(series)+'\n').encode())) as request:
                capture._vm()
            row = list(iter_records(capture.writer.path))[0]
            self.assertEqual(row['response'], [series])
            self.assertIn('traces.servicegraph', row['params']['match[]'])
            self.assertIn('edge_server="checkoutservice"', row['params']['match[]'])
            self.assertGreaterEqual(row['received_at_ns'], row['requested_at_ns'])
            self.assertLessEqual(request.call_args.kwargs['timeout'], 2)

    def test_stop_prevents_new_poll_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = self.capture(directory)
            capture._stop.set()
            with patch('evaluation.timing_capture.r.kube') as kube, patch('evaluation.timing_capture.urllib.request.urlopen') as http:
                capture._poll()
            kube.assert_not_called()
            http.assert_not_called()

    def test_pagination_checks_cancel_and_preserves_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = self.capture(directory)
            capture.api = 'http://fixture.invalid'
            pages = [{'data': {'samples': [{'value': 1}]}, 'next_token': 'next'}, {'data': {'samples': [{'value': 2}], 'query': {'source': 'trace'}, 'snapshot': {'id': 's'}}}]
            with patch('evaluation.timing_capture.urllib.request.urlopen', side_effect=[io.BytesIO(json.dumps(x).encode()) for x in pages]) as request:
                response = capture._api_pages('/api/v1/metrics', {}, 'samples')
            self.assertEqual(response['data']['samples'], [{'value': 1}, {'value': 2}])
            self.assertEqual(response['data']['pages'], 2)
            self.assertEqual(response['data']['snapshot'], {'id': 's'})
            self.assertTrue(all(x.kwargs['timeout'] <= 2 for x in request.call_args_list))

    def test_cancel_between_pages_starts_no_second_request(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = self.capture(directory)
            capture.api = 'http://fixture.invalid'
            def first(*args, **kwargs):
                capture._stop.set()
                return io.BytesIO(json.dumps({'data': {'samples': []}, 'next_token': 'next'}).encode())
            with patch('evaluation.timing_capture.urllib.request.urlopen', side_effect=first) as request:
                with self.assertRaises(InterruptedError):
                    capture._api_pages('/api/v1/metrics', {}, 'samples')
            self.assertEqual(request.call_count, 1)

    def test_api_pagination_and_duration_window(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = self.capture(directory)
            capture.api = 'http://fixture.invalid'
            with patch.object(capture, '_api_pages', return_value={'data': {'samples': []}}) as pages:
                capture._api('servicegraph.request.duration')
            params = pages.call_args.args[1]
            self.assertEqual(params['window'], '60s')
            self.assertNotIn('aggregation', params)
            self.assertEqual(params['from'], params['to'])
            self.assertEqual(capture.status['streams']['api:servicegraph.request.duration']['nonempty'], 0)

    def test_fixed_point_is_retried_until_visible_then_advanced(self):
        # VictoriaMetrics removes a single range point younger than its 30s
        # latency offset. Moving the target on every poll can never see it.
        with tempfile.TemporaryDirectory() as directory:
            capture = self.capture(directory)
            capture.api = 'http://fixture.invalid'
            now = [100_000_000_000]
            def query(route, params, key):
                from datetime import datetime
                target = datetime.fromisoformat(params['from'].replace('Z', '+00:00')).timestamp()
                samples = [] if now[0]/1e9-target < 30 else [{'timestamp': params['from'], 'value': 1}]
                return {'data': {'samples': samples}}
            with patch('evaluation.timing_capture.time.time_ns', side_effect=lambda: now[0]), patch.object(capture, '_api_pages', side_effect=query):
                for seconds in (100, 105, 135, 140):
                    now[0] = seconds*1_000_000_000
                    capture._api('servicegraph.request.count')
            rows = [row for row in iter_records(capture.writer.path) if row['source'] == 'api']
            self.assertEqual([x['params']['from'] for x in rows[:3]], [rows[0]['params']['from']]*3)
            self.assertNotEqual(rows[2]['params']['from'], rows[3]['params']['from'])
            self.assertEqual(len(rows[2]['response']['data']['samples']), 1)
            self.assertEqual(capture.status['streams']['api:servicegraph.request.count']['nonempty'], 1)
            self.assertEqual(rows[2]['evaluation_age_seconds'], 35)
            self.assertEqual(rows[2]['params']['step'], '15s')

    def test_empty_fixed_point_expires_without_fabricating_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = self.capture(directory)
            capture.api = 'http://fixture.invalid'
            now = [100_000_000_000]
            with patch('evaluation.timing_capture.time.time_ns', side_effect=lambda: now[0]), patch.object(capture, '_api_pages', return_value={'data': {'samples': []}}):
                capture._api('servicegraph.request.duration')
                now[0] += 90_000_000_000
                capture._api('servicegraph.request.duration')
            rows = [row for row in iter_records(capture.writer.path) if row['source'] == 'api']
            self.assertNotEqual(rows[0]['params']['from'], rows[1]['params']['from'])
            self.assertEqual(rows[1]['previous_probe']['reason'], 'empty_after_timeout')
            self.assertEqual(capture.status['streams']['api:servicegraph.request.duration']['nonempty'], 0)
            self.assertFalse(capture.stop()['valid'])

    def test_failed_start_closes_forward(self):
        closed = []
        @contextlib.contextmanager
        def forward(*args):
            try:
                yield 'http://fixture.invalid'
            finally:
                closed.append(True)
        with tempfile.TemporaryDirectory() as directory:
            capture = self.capture(directory)
            with patch('evaluation.timing_capture.r.forward', side_effect=forward), patch('evaluation.timing_capture.r.kube_json', return_value={'items': []}):
                with self.assertRaisesRegex(ValueError, 'VictoriaMetrics'):
                    capture.start()
            self.assertEqual(closed, [True])
            self.assertFalse(capture.status['valid'])

    def test_worker_alive_invalidates_without_closing_its_contexts(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = self.capture(directory)
            capture._thread = SimpleNamespace(join=lambda **kw: None, is_alive=lambda: True)
            closed = []
            capture._stack.callback(lambda: closed.append(True))
            self.assertFalse(capture.stop()['valid'])
            self.assertEqual(closed, [])
            self.assertIn('still alive', str(capture.status['errors']))
            self.assertFalse(capture.status['worker_stopped'])

    def test_truncation_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = self.capture(directory)
            with patch('evaluation.timing_capture.r.kube', side_effect=[reader_result('3:2\ne30='), reader_result('3:0\n')]):
                capture._tail('agent', pod(), 'metrics.json')
                with self.assertRaisesRegex(ValueError, 'truncation'):
                    capture._tail('agent', pod(), 'metrics.json')

    def test_clock_bounds_include_offset_and_rtt(self):
        repair = SimpleNamespace(pods={'agent': pod()})
        with patch('evaluation.timing_capture.r.kube', return_value=SimpleNamespace(stdout='1100000000')), patch('evaluation.timing_capture.time.time_ns', side_effect=[1000000000, 1200000000]*3):
            result = clock_samples(repair, node_name='node-a')
        self.assertTrue(result['valid'])
        self.assertAlmostEqual(result['clock_uncertainty_seconds'], .1)
        self.assertEqual(len(result['samples']), 3)

    def test_persistent_clock_stream_round_trips_real_peer(self):
        import sys
        import time
        with clock_stream([sys.executable, '-u', '-c', CLOCK_PEER]) as exchange:
            for _ in range(3):
                started = time.time_ns()
                received = exchange()
                ended = time.time_ns()
                self.assertLessEqual(started, received)
                self.assertLessEqual(received, ended)

    def test_persistent_clock_stream_rejects_wrong_sequence(self):
        import sys
        peer = 'import sys; [print("999 123", flush=True) for _ in sys.stdin]'
        with self.assertRaisesRegex(ValueError, 'unmatched'):
            with clock_stream([sys.executable, '-u', '-c', peer]):
                self.fail('malformed handshake reached measurement')

    def test_clock_wrong_node_cannot_pass(self):
        self.assertFalse(clock_samples(SimpleNamespace(pods={'agent': pod()}), node_name='different')['valid'])


if __name__ == '__main__':
    unittest.main()
