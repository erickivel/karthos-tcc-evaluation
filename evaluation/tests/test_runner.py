import json
import tempfile
import unittest
import io
from unittest.mock import patch
from urllib.error import HTTPError
from pathlib import Path

from evaluation.runner import attempt_statuses, get_json


class AttemptReportTests(unittest.TestCase):
    def test_api_rejection_preserves_the_public_error_body(self):
        error = HTTPError('http://api/metrics', 400, 'Bad Request', {},
                          io.BytesIO(b'{"error":"aggregation is unsupported"}'))
        with patch('evaluation.runner.urllib.request.urlopen', side_effect=error):
            with self.assertRaisesRegex(RuntimeError, 'aggregation is unsupported'):
                get_json('http://api/metrics')

    def test_failed_attempts_are_listed_once(self):
        with tempfile.TemporaryDirectory() as root:
            run_dir = Path(root)
            for relative in ('smoke/r1-boutique-trace/status.json',
                             'measured/r1-boutique-trace/status.json',
                             'failed-attempts/r1-boutique-trace-1/status.json'):
                path = run_dir / relative
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps({'path': relative}))
            actual = attempt_statuses(run_dir)
            self.assertEqual(len(actual), 3)
            self.assertEqual({entry['path'] for entry in actual}, {
                'smoke/r1-boutique-trace/status.json',
                'measured/r1-boutique-trace/status.json',
                'failed-attempts/r1-boutique-trace-1/status.json',
            })


class BinaryCommandTests(unittest.TestCase):
    def test_binary_command_does_not_decode_stderr_as_utf8(self):
        import sys
        from evaluation import runner
        result=runner.cmd([sys.executable, '-c', "import sys;sys.stdout.buffer.write(b'valid\\n');sys.stderr.buffer.write(bytes([253]))"], binary=True, quiet=True)
        self.assertEqual(result.stdout,b'valid\n')
        self.assertEqual(result.stderr,bytes([253]))
