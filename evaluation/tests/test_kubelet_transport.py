import http.server
import socketserver
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

from evaluation.kubelet_transport import kubelet_request


class KubeletTransportTests(unittest.TestCase):
    def test_unix_socket_read_uses_exact_node_path_and_preserves_error_body(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.server.paths.append(self.path)
                self.send_response(200 if 'node-a' in self.path else 403)
                self.end_headers()
                self.wfile.write(b'original bytes\n')
            def log_message(self, *_):
                pass
        with tempfile.TemporaryDirectory(dir='/tmp') as folder:
            path = str(Path(folder)/'proxy.sock')
            with socketserver.UnixStreamServer(path, Handler) as server:
                server.paths = []
                thread = threading.Thread(target=server.serve_forever)
                thread.start()
                try:
                    good = kubelet_request(path, 'node-a', 2)
                    bad = kubelet_request(path, 'node-b', 2)
                    with self.assertRaises(ValueError):
                        kubelet_request(path, '../../secrets', 2)
                finally:
                    server.shutdown()
                    thread.join()
            self.assertEqual(server.paths, ['/api/v1/nodes/node-a/proxy/stats/summary',
                                            '/api/v1/nodes/node-b/proxy/stats/summary'])
            self.assertEqual(good.returncode, 0)
            self.assertEqual(good.stdout, b'original bytes\n')
            self.assertEqual(bad.returncode, 403)
            self.assertEqual(bad.stdout, good.stdout)

    def test_each_transport_failure_keeps_exception_and_stage(self):
        for stage in ('connect', 'request', 'headers', 'body'):
            with self.subTest(stage=stage):
                connection = MagicMock()
                response = connection.getresponse.return_value.__enter__.return_value
                response.read.return_value = b'{}'
                response.status = 200
                failure = TimeoutError('timed out')
                method = {'connect': connection.connect, 'request': connection.request,
                          'headers': connection.getresponse, 'body': response.read}[stage]
                method.side_effect = failure
                with patch('evaluation.kubelet_transport.UnixHTTPConnection', return_value=connection):
                    with self.assertRaises(TimeoutError) as raised:
                        kubelet_request('/private/api.sock', 'node-a', 5.)
                self.assertIs(raised.exception, failure)
                self.assertEqual(str(raised.exception), 'timed out')
                diagnostic = raised.exception.transport_diagnostic
                self.assertEqual(diagnostic['failed_stage'], stage)
                self.assertEqual(diagnostic['timeout_seconds'], 5.)
                self.assertGreaterEqual(diagnostic['elapsed_seconds'], 0)
                connection.close.assert_called_once()

    def test_success_reports_all_stage_timings_without_changing_response_bytes(self):
        connection = MagicMock()
        response = connection.getresponse.return_value.__enter__.return_value
        response.read.return_value = b'{"original":true}'
        response.status = 200
        with patch('evaluation.kubelet_transport.UnixHTTPConnection', return_value=connection):
            result = kubelet_request('/private/api.sock', 'node-a', 5.)
        self.assertEqual(result.stdout, b'{"original":true}')
        self.assertEqual(set(result.transport_diagnostic['stages']),
                         {'connect', 'request', 'headers', 'body'})
        self.assertNotIn('failed_stage', result.transport_diagnostic)
