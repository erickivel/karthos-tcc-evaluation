import contextlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from evaluation.gke import minimal_readiness as m
from evaluation.gke.models import write


class MinimalReadinessTests(unittest.TestCase):
    def context(self, root):
        plan = root / 'setup'
        plan.mkdir()
        write(plan / 'resolved-plan.json', {'metric_assignments': {'servicegraph.request.count': 'traces.servicegraph'}})
        pod = {'metadata': {'name': 'fixture'}, 'status': {'phase': 'Running', 'conditions': [{'type': 'Ready', 'status': 'True'}]}}
        def kube(*args, **kwargs):
            if args[1] == 'configmap':
                payload={'metadata':{'name':'karthos-source-contract','uid':'contract-uid'},
                         'data':{'source-plan.json':json.dumps({'metric_assignments':{'servicegraph.request.count':'traces.servicegraph'}})}}
            elif args[1] == 'deployments,statefulsets,daemonsets':
                payload={'items':[{'kind':'Deployment','metadata':{'name':name,'generation':2},
                                  'spec':{'replicas':1},'status':{'observedGeneration':2,'updatedReplicas':1,
                                                               'readyReplicas':1,'availableReplicas':1,'replicas':1}}
                                 for name in ('karthos-backend','karthos-gateway','karthos-agent')]}
            else:
                payload={'items':[dict(pod,metadata={'name':name+'-fixture'}) for name in ('karthos-backend','karthos-gateway','karthos-agent')]}
            return SimpleNamespace(stdout=json.dumps(payload))
        ctx = SimpleNamespace(result=root, run_id='run', deadline=10**12,
                              config={'limits': {'cleanup_reserve_seconds': 1200}},
                              state={'terraform': {'foundation': {'evidence_bucket': 'fixture-evidence'}}},
                              kube=kube, write=write)
        return ctx, SimpleNamespace(cell=SimpleNamespace(namespace='demo'), setup_path=plan)

    @contextlib.contextmanager
    def mocks(self, responder):
        with patch('evaluation.runner.forward', return_value=contextlib.nullcontext('http://fixture')), \
             patch.object(m, '_json', side_effect=responder), \
             patch.object(m.export, 'upload', return_value={'verified': True}) as upload:
            yield upload

    def test_empty_traffic_free_metric_read_does_not_block_core_readiness(self):
        with tempfile.TemporaryDirectory() as folder:
            ctx, driver = self.context(Path(folder))
            def response(base, route, params, deadline):
                key = 'metrics' if route.endswith('catalog') else 'nodes' if route.endswith('nodes') else 'samples'
                return {'data': {key: [] if key == 'samples' else [{'name': 'fixture'}]}}
            with self.mocks(response):
                report = m.check_cell(ctx, driver, {'cell': 'boutique-trace', 'source': 'trace', 'repeat':1}, 'attempt-1')
            self.assertTrue(report['usable'])
            self.assertEqual(report['checks']['available-telemetry']['state'], 'unavailable')
            self.assertFalse(report['traffic_generated'])

    def test_catalog_error_does_not_skip_graph_or_telemetry(self):
        with tempfile.TemporaryDirectory() as folder:
            ctx, driver = self.context(Path(folder));routes=[]
            def response(base, route, params, deadline):
                routes.append(route)
                if route.endswith('catalog'):
                    raise TimeoutError('catalog timeout')
                return {'data': {'nodes': [{'id': 'fixture'}], 'samples': []}}
            with self.mocks(response):
                report = m.check_cell(ctx, driver, {'cell': 'boutique-trace', 'source': 'trace', 'repeat':1}, 'attempt-1')
            self.assertFalse(report['usable'])
            self.assertEqual(report['checks']['graph']['state'], 'passed')
            self.assertIn('/api/v1/metrics', routes)

    def test_export_failure_is_explicit_safety_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            ctx, driver = self.context(Path(folder))
            with self.mocks(lambda *args: {'data': {'nodes': [{}], 'metrics': [{}], 'samples': []}}) as upload:
                upload.return_value = {'verified': False}
                with self.assertRaises(m.MinimalReadinessSafetyError):
                    m.check_cell(ctx, driver, {'cell': 'boutique-trace', 'source': 'trace', 'repeat':1}, 'attempt-1')

    def test_completed_jobs_are_not_active_deployment_failures(self):
        ready={'metadata':{'name':'app'},'status':{'phase':'Running','conditions':[{'type':'Ready','status':'True'}]}}
        completed={'metadata':{'name':'job'},'status':{'phase':'Succeeded'}}
        self.assertTrue(m._pod_state({'items':[ready,completed]})['valid'])
        self.assertFalse(m._pod_state({'items':[completed]})['valid'])

    def test_wrong_source_is_recorded_independently(self):
        with tempfile.TemporaryDirectory() as folder:
            ctx, driver = self.context(Path(folder))
            with self.mocks(lambda *args: {'data': {'nodes': [{}], 'metrics': [{}], 'samples': []}}):
                report=m.check_cell(ctx,driver,{'cell':'boutique-mesh','source':'mesh','repeat':1},'attempt-1')
            self.assertFalse(report['usable'])
            self.assertEqual(report['checks']['source-configuration']['evidence']['actual'],'traces.servicegraph')
            self.assertEqual(report['checks']['graph']['state'],'passed')
