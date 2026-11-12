import unittest
from evaluation.observers import observer_config, observer_patch, service_patch


class ObserverTests(unittest.TestCase):
    def test_all_nginx_temporary_paths_support_non_root_demo_pods(self):
        config=observer_config('frontend',8080)
        for kind in ('client_body','proxy','fastcgi','uwsgi','scgi'):
            self.assertIn(kind+'_temp_path /tmp/',config)

    def test_grpc_observer_preserves_trailers_and_disables_retries(self):
        config = observer_config('checkout', 5050, grpc=True)
        self.assertIn('grpc_pass grpc://127.0.0.1:5050', config)
        self.assertIn('grpc_next_upstream off', config)
        self.assertIn('$upstream_trailer_grpc_status', config)
        self.assertIn('$http_x_evaluation_request_id', config)
        self.assertIn('escape=json', config)

    def test_observer_patch_is_an_extra_container_with_bounded_resources(self):
        patch = observer_patch('checkout', 'nginx@sha256:test')
        pod = patch['spec']['template']['spec']
        self.assertEqual(pod['containers'][0]['name'], 'eval-observer')
        self.assertIn('limits', pod['containers'][0]['resources'])
        self.assertEqual(pod['volumes'][0]['configMap']['name'], 'eval-observer-checkout')

    def test_service_target_port_is_changed_without_changing_public_port(self):
        service = {'spec': {'ports': [{'name': 'tcp-service', 'port': 8080, 'targetPort': 8080}]}}
        patch = service_patch(service, grpc=True)
        self.assertEqual(patch['spec']['ports'][0]['port'], 8080)
        self.assertEqual(patch['spec']['ports'][0]['targetPort'], 18080)
        self.assertEqual(patch['spec']['ports'][0]['appProtocol'], 'grpc')

    def test_gke_observer_rollout_replaces_pod_then_restores_strategy(self):
        import json
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch
        from evaluation import pilot

        cell = SimpleNamespace(demo='astronomy', namespace='fixture')
        strategy = {'type': 'RollingUpdate', 'rollingUpdate': {'maxSurge': '25%', 'maxUnavailable': '25%'}}
        service = {'spec': {'ports': [{'name': 'http', 'port': 8080}]}}
        deployment = {'spec': {'strategy': strategy}}
        with (patch.object(pilot, 'observer_targets', return_value=[('frontend', 'frontend-proxy', False)]),
              patch.object(pilot.r, 'kube_json', side_effect=[service, deployment, service, deployment]),
              patch.object(pilot.r, 'kube') as kube, patch.object(pilot.r, 'write'),
              patch.object(pilot.r, 'apply'), patch.dict(pilot.r.CONFIG, {'observer_image': 'fixture'})):
            pilot.install_observers(cell, Path('.'), setup_no_surge=True)

        patch_observer, rollout, restore, patch_service = [c.args for c in kube.call_args_list]
        self.assertEqual(json.loads(patch_observer[-1])['spec']['strategy']['rollingUpdate'],
                         {'maxSurge': 0, 'maxUnavailable': 1})
        self.assertEqual(rollout[:3], ('rollout', 'status', 'deployment/frontend-proxy'))
        self.assertEqual(json.loads(restore[-1]),
                         [{'op': 'replace', 'path': '/spec/strategy', 'value': strategy}])
        self.assertEqual(patch_service[:3], ('patch', 'service', 'frontend-proxy'))
