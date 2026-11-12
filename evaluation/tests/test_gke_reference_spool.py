import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from types import SimpleNamespace
from unittest.mock import Mock

from evaluation.gke.reference_spool import EXPORT, decode_snapshot, read_spool


class ReferenceSpoolTests(unittest.TestCase):
    def test_snapshot_seals_complete_original_lines_and_reports_partial_tail(self):
        raw = b'{"record":1}\n{"record":2}\n'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'records.ndjson'
            path.write_bytes(raw+b'{"incomplete":')
            result = subprocess.run([sys.executable, '-c', EXPORT, str(path)], capture_output=True, check=True)
            body, seal = decode_snapshot(result.stdout, result.stderr)
        self.assertEqual(body, raw)
        self.assertEqual(seal['complete_bytes'], len(raw))
        self.assertEqual(seal['partial_line_bytes'], len(b'{"incomplete":'))
        seal['sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'checksum'):
            decode_snapshot(result.stdout, json.dumps(seal).encode())

    def test_read_retries_timeout_and_retains_attempt_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/'records.ndjson'
            source.write_bytes(b'{}\n')
            response = subprocess.run([sys.executable, '-c', EXPORT, str(source)], capture_output=True, check=True)
        kube = Mock(side_effect=[subprocess.TimeoutExpired('kubectl', 120), response])
        data, seal = read_spool(SimpleNamespace(kube=kube), 'demo', 'expected-pod')
        self.assertEqual(data, b'{}\n')
        self.assertEqual(len(seal['attempts']), 1)
        self.assertIn('timed out', seal['attempts'][0]['error'])
        self.assertEqual(kube.call_count, 2)
        self.assertEqual(kube.call_args.args[:7], ('exec', '-n', 'demo', 'expected-pod', '-c', 'reference', '--'))

    def test_snapshot_rejects_oversize_file_before_transfer(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'records.ndjson'
            with path.open('wb') as source:
                source.truncate(256*1024**2+1)
            result = subprocess.run([sys.executable, '-c', EXPORT, str(path)], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'byte bound', result.stderr)
