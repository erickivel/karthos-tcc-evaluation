"""Offline regression sources for independent attempts and sealed continuation."""
import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from evaluation.gke import export, recoverable_run as recovery
from evaluation.gke.models import sha, write


class RecoverableRunTests(unittest.TestCase):
    def test_first_valid_attempt_keeps_numerical_disagreement(self):
        attempts = [dict(attempt_id='w-a1', execution='complete', evidence_valid=True,
                         state='complete', export={'verified':True}, numerical_agreement='fail'),
                    dict(attempt_id='w-a2', execution='complete', evidence_valid=True,
                         state='complete', export={'verified':True}, numerical_agreement='pass')]
        row = recovery._row({'id':'w'}, attempts)
        self.assertEqual(row['representative_attempt'], 'w-a1')
        self.assertEqual(row['numerical_agreement'], 'fail')
        self.assertEqual(row['attempts'], attempts)
        self.assertEqual(row['attempt_count'], 2)

    def test_missing_evidence_is_not_selected_over_later_valid_attempt(self):
        attempts = [dict(attempt_id='w-a1', execution='complete', evidence_valid=False, state='failed'),
                    dict(attempt_id='w-a2', execution='complete', evidence_valid=True, state='complete')]
        row = recovery._row({'id':'w'}, attempts)
        self.assertEqual(row['representative_attempt'], 'w-a2')
        self.assertEqual(row['first_attempt_state'], 'failed')
        self.assertTrue(row['retried'])

    def test_all_invalid_attempts_remain_inspectable(self):
        attempts = [dict(attempt_id='w-a1', execution='incomplete', evidence_valid=False, state='failed'),
                    dict(attempt_id='w-a2', execution='complete', evidence_valid=False, state='failed')]
        row = recovery._row({'id':'w'}, attempts)
        self.assertEqual(row['representative_attempt'], 'w-a2')
        self.assertFalse(row['evidence_valid'])
        self.assertEqual(len(row['attempts']), 2)

    def test_forecast_requires_finite_positive_reviewed_estimates(self):
        config = {'readiness':{'phase_estimates_seconds':{}},
                  'limits':{'cleanup_reserve_seconds':80,'infrastructure_seconds':43200}}
        report = recovery.planning_forecast(config, [])
        self.assertFalse(report['fits'])
        self.assertIn('provision', report['missing'])
        config['readiness']['phase_estimates_seconds'] = {name:1 for name in report['missing']}
        config['readiness']['phase_estimates_seconds']['export'] = float('nan')
        self.assertFalse(recovery.planning_forecast(config, [])['fits'])

    def test_forecast_reuses_readiness_when_same_deployment_is_reused(self):
        estimates = dict(provision=10, rebuild=20, setup=30, capture_setup_restore=40,
                         export=50, history=60, retry_reserve=70)
        config = {'readiness':{'phase_estimates_seconds':estimates},
                  'limits':{'cleanup_reserve_seconds':80,'infrastructure_seconds':43200}}
        matrix = [dict(id='smoke', repeat=1, cell='boutique-trace', phase='smoke', duration_seconds=60),
                  dict(id='baseline', repeat=1, cell='boutique-trace', phase='baseline', duration_seconds=300)]
        report = recovery.planning_forecast(config, matrix)
        # Same-cell smoke and baseline share one deployment/readiness receipt.
        expected = 10+30+120+50+(60+300)+2*(60+90+180+40+50)+70+80
        self.assertEqual(report['seconds'], expected)
        self.assertEqual(report['setup_groups'], 1)

    def manifest(self):
        return dict(protocol_definition={'id':'thesis-qualification-v4'}, product_pins={'revision':'abc'},
                    image_lock={'backend':'image@sha256:abc'}, thresholds={'count':0},
                    implementation_sha256='implementation-1', matrix=[{'id':'w'}])

    def checkpoint(self, path, manifest, records=None):
        write(path/'checkpoint.json', {'version':4,'run_id':'old',
              'binding':recovery.binding(manifest),'records':records or [],'reports':{}})
        export.seal(path)
        return {'verified':True,'uri':'gs://private-evidence/run/checkpoint/',
                'checkpoint_sha256':sha(path/'checkpoint.json'),'seal_sha256':sha(path/'GKE-SHA256SUMS')}

    def test_continuation_rejects_changed_implementation(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'checkpoint';path.mkdir()
            manifest=self.manifest();receipt=self.checkpoint(path,manifest)
            receipt_path=Path(directory)/'receipt.json';write(receipt_path,receipt)
            changed=copy.deepcopy(manifest);changed['implementation_sha256']='implementation-2'
            with self.assertRaisesRegex(ValueError,'differs'):
                recovery.load_continuation(path,receipt_path,changed)

    def test_continuation_rejects_forged_receipt_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'checkpoint';path.mkdir()
            manifest=self.manifest();receipt=self.checkpoint(path,manifest)
            receipt['checkpoint_sha256']='0'*64
            receipt_path=Path(directory)/'receipt.json';write(receipt_path,receipt)
            with self.assertRaisesRegex(ValueError,'checksum mismatch'):
                recovery.load_continuation(path,receipt_path,manifest)

    def test_continuation_rejects_duplicate_window_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'checkpoint';path.mkdir()
            manifest=self.manifest();receipt=self.checkpoint(path,manifest,[{'id':'w'},{'id':'w'}])
            receipt_path=Path(directory)/'receipt.json';write(receipt_path,receipt)
            with self.assertRaisesRegex(ValueError,'duplicate or foreign'):
                recovery.load_continuation(path,receipt_path,manifest)

    def test_transition_does_not_confuse_restoration_with_worker_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);(path/'structured').mkdir()
            write(path/'structured/status.json',{'cleanup':{'restored':True},
                  'workers_stopped':False,'proxy_stopped':True,'errors':[]})
            ctx=SimpleNamespace(result=path,write=write,
                                kube=lambda *a,**k:SimpleNamespace(returncode=0,stdout=''))
            driver=SimpleNamespace(paths={'w-a1':path})
            with patch('evaluation.gke.provision.require_context'), \
                 patch('evaluation.gke.provision.assert_project_lease'), \
                 patch('evaluation.gke.recoverable_run._generator_barrier',return_value={'safe':True}):
                result=recovery.transition(ctx,driver,{'id':'w-a1','logical_id':'w'}, {})
            self.assertFalse(result['safe'])
            self.assertIn('termination not verified',result['error']['message'])
            self.assertTrue((path/'transition.json').exists())

    def test_transition_retains_sample_errors_without_blocking_stopped_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);(path/'structured').mkdir()
            write(path/'structured/status.json',{'cleanup':{'restored':True},
                  'workers_stopped':True,'proxy_stopped':True,'errors':['reference timeout']})
            ctx=SimpleNamespace(result=path,write=write,
                                kube=lambda *a,**k:SimpleNamespace(returncode=0,stdout=''))
            driver=SimpleNamespace(paths={'w-a1':path})
            with patch('evaluation.gke.provision.require_context'), \
                 patch('evaluation.gke.provision.assert_project_lease'), \
                 patch('evaluation.gke.recoverable_run._generator_barrier',return_value={'safe':True}):
                result=recovery.transition(ctx,driver,{'id':'w-a1','logical_id':'w'}, {})
            self.assertTrue(result['safe'])
            self.assertEqual(result['conditions']['capture']['measurement_errors'],['reference timeout'])
