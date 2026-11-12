import unittest
from evaluation.capture import stream_targets


class CaptureTests(unittest.TestCase):
    def test_streams_regular_and_restartable_init_containers(self):
        pods = {'items': [{'metadata': {'name': 'p', 'namespace': 'demo'},
                           'status': {'phase': 'Running'},
                           'spec': {'containers': [{'name': 'app'}, {'name': 'eval-observer'}],
                                    'initContainers': [{'name': 'istio-init'},
                                                       {'name': 'istio-proxy', 'restartPolicy': 'Always'}]}}]}
        self.assertEqual(stream_targets(pods), [('demo', 'p', 'app'), ('demo', 'p', 'eval-observer'),
                                              ('demo', 'p', 'istio-proxy')])


class ContinuousCapturePollingTests(unittest.TestCase):
    def test_continuous_capture_keeps_terminating_gateway_in_inventory_only(self):
        import contextlib
        import json
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch
        from evaluation.capture import ContinuousCapture

        def gateway(name, **metadata):
            return {'metadata': {'name': name, 'namespace': 'karthos-system', **metadata},
                    'status': {'phase': 'Running', 'conditions': [{'type': 'Ready', 'status': 'True'}]},
                    'spec': {'containers': [{'name': 'gateway'}]}}

        pods = {'items': [gateway('karthos-gateway-old', deletionTimestamp='2026-11-06T00:14:59Z'),
                          gateway('karthos-gateway-new')]}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            capture = ContinuousCapture(SimpleNamespace(namespace='app'), path)
            poller = MagicMock()
            poller.status.return_value = {'stream': 'karthos-system__karthos-gateway-new__gateway'}
            inventories = [{'items': []}, pods, {'items': [{'metadata': {'name': 'node'}}]}]
            with (patch('evaluation.capture.r.kube_json', side_effect=inventories),
                  patch('evaluation.capture.PollingLogCapture', return_value=poller) as logs,
                  patch('evaluation.capture.r.forward', return_value=contextlib.nullcontext('http://fixture')) as forward,
                  patch.object(capture, '_sample')):
                capture.start()
                capture.stop()
            self.assertEqual(json.loads((path/'pods-before-karthos-system.json').read_text()), pods)
            self.assertEqual(capture.collector_pods, [('karthos-system', 'karthos-gateway-new')])
            self.assertEqual(logs.call_count, 1)
            self.assertEqual(forward.call_args.args[0], 'pod/karthos-gateway-new')
            self.assertEqual(len(stream_targets(pods)), 2)

    def test_partial_setup_stops_already_started_pollers(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch
        from evaluation.capture import ContinuousCapture
        pods={'items':[{'metadata':{'name':'frontend','namespace':'app'},'status':{'phase':'Running'},'spec':{'containers':[{'name':'app'}]}}]}
        with tempfile.TemporaryDirectory() as temp:
            poller=MagicMock()
            poller.start.return_value=poller
            poller.status.return_value={'stream':'app__frontend__app','exited_before_stop':False,'returncode':0,'errors':[],'polls':2,'mode':'polling'}
            with patch('evaluation.capture.r.kube_json',side_effect=[pods,RuntimeError('setup failed')]), patch('evaluation.capture.PollingLogCapture',return_value=poller):
                capture=ContinuousCapture(SimpleNamespace(namespace='app'),Path(temp))
                with self.assertRaisesRegex(RuntimeError,'setup failed'): capture.start()
            poller.start.assert_called_once()
            poller.stop.assert_called_once()
            self.assertTrue((Path(temp)/'capture-status.json').exists())

    def test_stop_preserves_status_schema_and_is_idempotent(self):
        import json
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from evaluation.capture import ContinuousCapture
        with tempfile.TemporaryDirectory() as temp:
            capture=ContinuousCapture(SimpleNamespace(namespace='app'),Path(temp))
            poller=MagicMock()
            poller.status.return_value=dict(stream='ns__pod__container',returncode=0,errors=[],error=None,exited_before_stop=False,mode='polling',polls=2)
            capture.log_captures=[poller]
            capture.stop()
            capture.stop()
            poller.stop.assert_called_once()
            state=json.loads((Path(temp)/'capture-status.json').read_text())
            self.assertTrue(state['sampler_stopped'])
            self.assertEqual(state['streams'],[poller.status.return_value])
