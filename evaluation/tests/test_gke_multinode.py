"""Offline multi-node capture contract; no cluster commands are executed."""
import copy
import contextlib
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from evaluation import structured_capture as s
from evaluation.timing_capture import AvailabilityCapture, clock_samples


def pod(node, suffix=''):
    return {'metadata': {'name': 'karthos-agent-'+node+suffix, 'uid': 'uid-'+node+suffix,
            'namespace': s.NS, 'annotations': {s.OWNER: 'owner'}},
            'spec': {'nodeName': node}, 'status': {'phase': 'Running',
            'conditions': [{'type': 'Ready', 'status': 'True'}], 'containerStatuses': []}}


class MultinodeTests(unittest.TestCase):
    def capture(self, path):
        capture = s.RepairCapture(SimpleNamespace(namespace='app'), Path(path), time.time()+600,
                                  config={'expected_nodes': 3})
        capture.owner = 'owner'
        capture.nodes = ['n1', 'n2', 'n3']
        return capture

    def test_agent_inventory_requires_one_ready_owned_pod_on_each_node(self):
        with tempfile.TemporaryDirectory() as d:
            cap = self.capture(d)
            with patch.object(cap, '_get', return_value={'items': [pod(n) for n in cap.nodes]}):
                self.assertEqual(set(cap._pods('agent')), {'agent:n1', 'agent:n2', 'agent:n3'})
            for bad in ([pod('n1'), pod('n2')], [pod('n1'), pod('n1', '-other'), pod('n3')]):
                with patch.object(cap, '_get', return_value={'items': bad}), self.assertRaises(ValueError):
                    cap._pods('agent')
            bad = [pod(n) for n in cap.nodes]
            bad[1]['status']['conditions'][0]['status'] = 'False'
            with patch.object(cap, '_get', return_value={'items': bad}), self.assertRaises(ValueError):
                cap._pods('agent')

    def test_gateway_setup_waits_for_owned_replacement_to_be_ready(self):
        with tempfile.TemporaryDirectory() as d:
            cap = self.capture(d)
            ready = pod('n1')
            ready['metadata']['name'] = 'karthos-gateway-new'
            pending = copy.deepcopy(ready)
            pending['status']['phase'] = 'Pending'
            old = copy.deepcopy(ready)
            old['metadata'].update(name='karthos-gateway-old', uid='old', annotations={})
            starting = copy.deepcopy(ready)
            starting['status']['conditions'][0]['status'] = 'False'
            inventories = [{'items': [old]}, {'items': [old, pending]},
                           {'items': [starting]}, {'items': [ready]}]
            with patch.object(cap, '_get', side_effect=inventories) as get, patch.object(s.time, 'sleep'):
                observed = cap._wait_capture_pods('gateway', time.monotonic() + 30)
            self.assertEqual(observed, {'gateway': ready})
            self.assertEqual(get.call_count, 4)
            self.assertTrue(all(0 < call.kwargs['timeout'] <= 10 for call in get.call_args_list))

    def test_duplicate_owned_gateway_is_not_retried_as_readiness(self):
        with tempfile.TemporaryDirectory() as d:
            cap = self.capture(d)
            first, second = pod('n1'), pod('n1', '-other')
            first['metadata']['name'], second['metadata']['name'] = 'karthos-gateway-a', 'karthos-gateway-b'
            with patch.object(cap, '_get', return_value={'items': [first, second]}) as get:
                with self.assertRaisesRegex(ValueError, 'found 2'):
                    cap._wait_capture_pods('gateway', time.monotonic() + 30)
            self.assertEqual(get.call_count, 1)

    def test_clock_selects_explicit_agent_node_from_multinode_mapping(self):
        repair = SimpleNamespace(pods={'agent:'+n: pod(n) for n in ('n1', 'n2', 'n3')})
        with patch('evaluation.timing_capture.r.kube', side_effect=lambda *a, **k: SimpleNamespace(stdout=str(time.time_ns()))) as kube:
            self.assertTrue(clock_samples(repair, 'n2', tolerance_seconds=2)['valid'])
            self.assertEqual(kube.call_args.args[3], 'karthos-agent-n2')

    def test_availability_has_independent_per_node_streams(self):
        with tempfile.TemporaryDirectory() as d:
            repair = SimpleNamespace(pods={'agent:'+n: pod(n) for n in ('n1', 'n2', 'n3')})
            capture = AvailabilityCapture(SimpleNamespace(source='trace'), Path(d), repair)
            self.assertTrue({'agent:n1:metrics.json', 'agent:n2:metrics.json', 'agent:n3:metrics.json'} <= capture._expected)

    def test_gateway_trace_poll_can_run_while_metrics_poll_is_blocked(self):
        import threading
        with tempfile.TemporaryDirectory() as d:
            repair = SimpleNamespace(pods={'gateway': pod('n1'), 'agent:n1': pod('n1')})
            capture = AvailabilityCapture(SimpleNamespace(source='trace'), Path(d), repair)
            metrics_started = threading.Event()
            traces_started = threading.Event()
            agent_started = threading.Event()
            release = threading.Event()
            def poll(kind, _, filename, stream):
                if kind == 'gateway' and filename == 'metrics.json' and stream is not None:
                    metrics_started.set()
                    release.wait(3)
                elif kind == 'gateway' and filename == 'traces.json' and stream is not None:
                    traces_started.set()
                elif kind == 'agent:n1' and stream is not None:
                    agent_started.set()
            with (patch('evaluation.timing_capture.r.forward', return_value=contextlib.nullcontext('http://fixture.invalid')),
                  patch('evaluation.timing_capture.r.kube_json', return_value={'items': [{'metadata': {'name': 'victoria'}}]}),
                  patch('evaluation.timing_capture.CollectorTailStream.handshake') as handshake,
                  patch.object(capture, '_poll_remote'),
                  patch.object(capture, '_poll_collector', side_effect=poll)):
                capture.start()
                try:
                    self.assertEqual(handshake.call_count, 3)
                    self.assertTrue(agent_started.wait(2))
                    self.assertTrue(metrics_started.wait(2))
                    self.assertTrue(traces_started.wait(2))
                finally:
                    release.set()
                    capture.stop()
            self.assertTrue(capture.status['worker_stopped'])

    def test_each_kubelet_node_has_an_independent_loop_and_budget(self):
        with tempfile.TemporaryDirectory() as d:
            capture = s.FastCapture(SimpleNamespace(namespace='app'), Path(d))
            capture.sample_settings = {'expected_nodes': 3}
            capture.nodes = ['n1', 'n2', 'n3']
            specs = capture._stream_specs()
            kubelet = [x for x in specs if x[0] == 'kubelet']
            self.assertEqual([x[1] for x in kubelet], capture.nodes)
            self.assertEqual(len({x[2] for x in kubelet}), 3)

    def test_mesh_proxy_sampling_remains_independent_with_a_slow_target(self):
        import threading
        from evaluation.sample_records import iter_records

        with tempfile.TemporaryDirectory() as d:
            capture = s.FastCapture(SimpleNamespace(namespace='app', source='mesh'), Path(d))
            capture.sample_settings = {'expected_nodes': 3}
            capture.nodes = ['n1', 'n2', 'n3']
            capture.mesh_pods = [('app', 'checkout'), ('app', 'frontend'), ('app', 'frontend-proxy')]
            specs = [spec for spec in capture._stream_specs() if spec[0] == 'mesh']
            slow_started = threading.Event()
            checkout_release = threading.Event()
            fast_targets = threading.Event()
            fast_seen = set()

            def kube(*args, **kwargs):
                target = args[2].split('/')[6].split(':')[0]
                if target == 'checkout':
                    slow_started.set()
                    checkout_release.wait(3)
                else:
                    fast_seen.add(target)
                    if fast_seen == {'frontend', 'frontend-proxy'}:
                        fast_targets.set()
                return SimpleNamespace(returncode=0, stdout='metric 1\n')

            with patch.object(capture, '_stream_specs', return_value=specs), patch('evaluation.structured_capture.r.kube', side_effect=kube):
                driver = threading.Thread(target=capture._sample)
                driver.start()
                try:
                    self.assertTrue(slow_started.wait(1))
                    self.assertTrue(fast_targets.wait(1))
                finally:
                    capture.stop_event.set()
                    checkout_release.set()
                    driver.join(4)
            self.assertFalse(driver.is_alive())
            evidence = Path(d) / 'istio-samples.ndjson.gz'
            self.assertEqual({row['pod'] for row in iter_records(evidence)}, {'checkout','frontend','frontend-proxy'})
            self.assertTrue(capture.sample_writers[str(evidence)].validate()['valid'])
            self.assertEqual(capture.sampling_errors, [])

    def test_pod_node_migration_invalidates_identity(self):
        before = pod('n1'); after = copy.deepcopy(before); after['spec']['nodeName'] = 'n2'
        self.assertIn('collector node changed', s.pod_errors(before, after))

    def test_source_daemonset_requires_all_nodes_without_duplicates(self):
        pods = [pod(n) for n in ('n1', 'n2', 'n3')]
        for item in pods:
            item['metadata']['ownerReferences'] = [{'kind': 'DaemonSet', 'uid': 'source-ds', 'controller': True}]
        s.validate_source_coverage(pods, ['n1', 'n2', 'n3'])
        for bad in (pods[:2], pods+[pods[0]]):
            with self.assertRaises(ValueError):
                s.validate_source_coverage(bad, ['n1', 'n2', 'n3'])

    def test_receiver_wrong_node_fails_even_with_correct_uid(self):
        row = {'collector_uid': 'uid-n1', 'node_name': 'n2', 'status': 200,
               'requested_at_ns': 1000000000, 'received_at_ns': 2000000000, 'path': '/stats/summary'}
        self.assertIn('receiver node mismatch', s.receiver_errors([row], 'uid-n1', {}, node_name='n1'))

    def test_independent_archives_preserve_each_nodes_source_identity(self):
        with tempfile.TemporaryDirectory() as d:
            cap = self.capture(d)
            cap.pods = {'agent:'+n: pod(n) for n in cap.nodes}
            def archive(key, attempt):
                raw=cap._destination(key)/('raw-attempt-'+str(attempt));raw.mkdir(parents=True)
                return {'raw_directory': str(raw), 'errors': [], 'files': [],
                        'collector_uid': cap.pods[key]['metadata']['uid'],
                        'node_name': cap.pods[key]['spec']['nodeName']}
            with patch.object(cap, '_archive_attempt', side_effect=archive):
                for key in cap.pods:cap._archive(key)
            self.assertEqual(set(cap.state['components']), set(cap.pods))
            for node in cap.nodes:
                self.assertTrue((cap.root/'agent'/node/'raw').is_dir())
                self.assertEqual(cap.state['components']['agent:'+node]['collector_uid'], 'uid-'+node)

    def test_three_kubelet_acquisitions_run_concurrently(self):
        import threading
        with tempfile.TemporaryDirectory() as d:
            capture = s.FastCapture(SimpleNamespace(namespace='app'), Path(d))
            capture.sample_settings={'expected_nodes':3};capture.nodes=['n1','n2','n3']
            barrier=threading.Barrier(3);seen=[]
            def loop(stream,node,filename):
                if stream=='kubelet':
                    barrier.wait(timeout=2);seen.append(node)
            with patch.object(capture,'_loop',side_effect=loop):capture._sample()
            self.assertEqual(sorted(seen),capture.nodes)

    def test_ready_node_gate_rejects_missing_duplicate_or_unready_nodes(self):
        nodes={'items':[{'metadata':{'name':n,'uid':'node-uid-'+n},'status':{'conditions':[{'type':'Ready','status':'True'}]}} for n in ('n1','n2','n3')]}
        self.assertEqual(s.ready_nodes(nodes,3),['n1','n2','n3'])
        nodes['items'][1]['status']['conditions'][0]['status']='False'
        with self.assertRaises(ValueError):s.ready_nodes(nodes,3)

    def test_kubelet_sample_retains_api_node_uid(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            cap=s.FastCapture(SimpleNamespace(namespace='app'),Path(d));cap.node='n1';cap.node_uids={'n1':'node-uid-1'}
            response=SimpleNamespace(stdout=json.dumps({'node':{'nodeName':'n1'},'pods':[]}).encode(),stderr=b'',returncode=0)
            with patch('evaluation.structured_capture.r.kube',return_value=response):
                row={};cap._read_kubelet_json(row,'n1')
            self.assertEqual(row['node_uid'],'node-uid-1')
