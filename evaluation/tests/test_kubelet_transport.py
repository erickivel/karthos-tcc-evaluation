import http.server
import socketserver
import tempfile
import threading
import unittest
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
