import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from evaluation.protocol import Cell
from evaluation.timing_capture import AvailabilityCapture


class IdentityTransportTests(unittest.TestCase):
    def test_persistent_identity_transport_keeps_uid_guard_and_no_cli_poll(self):
        pod = dict(metadata=dict(name='agent-a', uid='uid-a'), spec=dict(nodeName='node-a'),
                   status=dict(containerStatuses=[dict(name='agent', restartCount=0)]))
        with tempfile.TemporaryDirectory() as directory:
            capture = AvailabilityCapture(Cell('boutique', 'trace', 1), Path(directory), SimpleNamespace(pods={'agent': pod}))
            capture._identity_socket = '/tmp/fixture.sock'
            with patch('evaluation.kubelet_transport.pod_request', create=True, return_value=pod) as request, patch('evaluation.timing_capture.r.kube') as kube, patch.object(capture, '_tail') as tail:
                capture._poll_collector('agent', pod)
                request.assert_called_once_with('/tmp/fixture.sock', 'karthos-system', 'agent-a', 5)
                kube.assert_not_called()
                tail.assert_called_once()
            changed = dict(pod, metadata=dict(name='agent-a', uid='replacement'))
            with patch('evaluation.kubelet_transport.pod_request', create=True, return_value=changed), patch.object(capture, '_tail') as tail:
                with self.assertRaises(ValueError): capture._poll_collector('agent', pod)
                tail.assert_not_called()
