import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from evaluation.structured_capture import FastCapture


class SamplerGapDiagnosticsTests(unittest.TestCase):
    def test_slow_acquisition_is_attributed_in_gap_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            capture = FastCapture(SimpleNamespace(namespace='fixture'), Path(folder))
            capture.stop_event = threading.Event()
            calls = []
            def sample(*args, **kwargs):
                calls.append(1)
                if len(calls) == 1:
                    time.sleep(.08)
                else:
                    capture.stop_event.set()
            with patch.dict(capture.PERIODS, {'kubelet': .01}), \
                 patch.object(capture, '_sample_once', side_effect=sample), \
                 patch.object(capture, '_sampling_status'):
                capture._loop('kubelet', 'node-a', 'samples.ndjson')
            gap, = capture.sampling_errors
            self.assertEqual(gap['error'], 'sampling gap')
            self.assertGreaterEqual(gap['previous_sample_seconds'], .07)
            self.assertGreaterEqual(gap['previous_status_seconds'], 0)
            self.assertGreaterEqual(gap['previous_wait_seconds'], 0)

if __name__ == '__main__':
    unittest.main()
