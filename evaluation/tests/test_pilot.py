import unittest
import tempfile
import contextlib
from pathlib import Path
from unittest.mock import patch
from evaluation.pilot import windows, metric_queries, freeze_thresholds, resume_fingerprint, extract_events
from evaluation.pilot import export_window
from evaluation.pilot import loggable_pod
from evaluation.pilot import generator_log_complete, capture_preroll_seconds
from evaluation.pilot import native_window_bounds
from evaluation.pilot import smoke_evidence_valid
from evaluation.pilot import cleanup_generator
from evaluation.pilot import reserve_window_budget
from evaluation.pilot import configure_generator_deadline
from evaluation.protocol import Cell


class PilotTests(unittest.TestCase):
    def test_resume_fingerprint_includes_versioned_yaml(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'evaluation'/'presets';folder.mkdir(parents=True)
            file=folder/'source.yaml';file.write_text('version: 1\n')
            initial=resume_fingerprint(root)
            self.assertIn('evaluation/presets/source.yaml',initial)
            file.write_text('version: 2\n')
            self.assertNotEqual(initial,resume_fingerprint(root))

    def test_deadline_is_rechecked_after_setup_before_generator_launch(self):
        config=dict(capture_preroll_seconds=60,artifact_export_seconds=180,
                    artifact_recovery_seconds=1800,telemetry_drain_seconds=90)
        job={'spec':{'template':{'spec':{'containers':[{'env':[]}]}}}}
        with self.assertRaises(TimeoutError):
            configure_generator_deadline(job,300,config,700,1000)
        configure_generator_deadline(job,300,config,900,1000)
        self.assertLessEqual(job['spec']['activeDeadlineSeconds'],900+1800)
        env=job['spec']['template']['spec']['containers'][0]['env']
        self.assertIn({'name':'EVAL_STUDY_DEADLINE_EPOCH','value':'1900'},env)

    def test_window_requires_normal_budget_but_recovery_can_extend_deadline(self):
        config=dict(capture_preroll_seconds=60,artifact_export_seconds=180,
                    artifact_recovery_seconds=1800,telemetry_drain_seconds=90)
        reserve_window_budget(300,config,remaining=900)
        with self.assertRaises(TimeoutError):reserve_window_budget(300,config,remaining=600)

    def test_cleanup_retains_unverified_generator(self):
        with tempfile.TemporaryDirectory() as tmp,patch('evaluation.pilot.r.kube') as kube:
            path=Path(tmp)
            cleanup_generator('load','demo',path,False)
            kube.assert_not_called()
            self.assertTrue((path/'generator-retained.json').exists())
            cleanup_generator('load','demo',path,True)
            self.assertEqual(kube.call_args.args[:3],('delete','job','load'))

    def test_smoke_keeps_application_failures_with_a_usable_oracle(self):
        window = dict(oracle_valid=True, source_valid=True, unexpected_outcomes=1, state='fail')
        self.assertTrue(smoke_evidence_valid([window]))
        self.assertFalse(smoke_evidence_valid([{**window, 'oracle_valid': False}]))
        self.assertFalse(smoke_evidence_valid([]))

    def test_native_bounds_exclude_preroll_and_identify_container_lifetime(self):
        pods = {'items': [{'status': {'containerStatuses': [{'name': 'load', 'state': {
            'terminated': {'startedAt': '2026-01-01T00:01:00Z',
                           'finishedAt': '2026-01-01T00:04:00Z'}}}]}}]}
        self.assertEqual(native_window_bounds(pods, 'load'),
                         ('2026-01-01T00:01:00Z', '2026-01-01T00:04:00Z'))
        self.assertIsNone(native_window_bounds(pods, 'other'))

    def test_log_completion_requires_verified_artifacts_not_successful_transport(self):
        good = dict(verified=True, semantic_complete=True)
        self.assertTrue(generator_log_complete(good))
        for changed in ({'verified': False}, {'semantic_complete': False}):
            self.assertFalse(generator_log_complete({**good, **changed}))
        self.assertFalse(generator_log_complete(dict(returncode=0,errors=[],polls=2,stopped=True)))

    def test_capture_preroll_covers_the_permitted_cached_history(self):
        config = {'capture_preroll_seconds': 60, 'thresholds': {'max_freshness_seconds': 60}}
        self.assertEqual(capture_preroll_seconds(config), 60)
        config['capture_preroll_seconds'] = 30
        with self.assertRaises(ValueError):
            capture_preroll_seconds(config)

    def test_generator_log_waits_for_the_named_container(self):
        pod = {'metadata': {'name': 'load-1'}, 'status': {'containerStatuses': [
            {'name': 'load', 'state': {'waiting': {'reason': 'ContainerCreating'}}}]}}
        self.assertIsNone(loggable_pod({'items': [pod]}, 'load'))
        for state in ({'running': {'startedAt': 'now'}}, {'terminated': {'exitCode': 0}}):
            pod['status']['containerStatuses'][0]['state'] = state
            self.assertEqual(loggable_pod({'items': [pod]}, 'load'), 'load-1')
            self.assertIsNone(loggable_pod({'items': [pod]}, 'other'))
        self.assertIsNone(loggable_pod({'items': []}, 'load'))

    def test_both_generators_keep_old_then_longer_and_swap_between_repeats(self):
        first = windows(1)
        second = windows(2)
        self.assertEqual([(w['generator'], w['duration_seconds']) for w in first],
                         [('k6', 180), ('k6', 300), ('locust', 180), ('locust', 300)])
        self.assertEqual(second[0]['generator'], 'locust')
        self.assertEqual([w['workload'] for w in second[:2]], ['legacy', 'checkout-heavy'])

    def test_queries_score_whole_window_not_default_last_minute(self):
        queries = metric_queries('2026-01-01T00:00:00Z', '2026-01-01T00:05:00Z', 300)
        duration = next(x for x in queries if x[0] == 'api-duration.json')
        self.assertEqual(duration[2]['window'], '300s')
        self.assertNotIn('aggregation', duration[2])
        cpu = next(x for x in queries if x[0] == 'api-cpu.json')
        self.assertNotIn('aggregation', cpu[2])

    def test_smoke_windows_respect_the_public_api_minimum(self):
        self.assertTrue(all(w['duration_seconds'] >= 60 for w in windows(1, smoke=True)))
        with self.assertRaises(ValueError):
            metric_queries('a', 'b', 30)

    def test_thresholds_are_not_fit_to_measurement_errors(self):
        frozen = freeze_thresholds({'request_relative_tolerance': .15}, [{'valid': True}] * 4)
        self.assertEqual(frozen['request_relative_tolerance'], .15)
        self.assertTrue(frozen['calibration']['completed_before_measurements'])
        with self.assertRaises(ValueError):
            freeze_thresholds({}, [])

    def test_console_events_are_decoded_without_changing_payload(self):
        self.assertEqual(extract_events('EVAL_ATTEMPT {"request_id":"a"}\n', 'ATTEMPT'),
                         [{'request_id': 'a'}])
        self.assertEqual(extract_events('time=x msg="EVAL_REQUEST {\\"status\\":200}" source=console', 'REQUEST'),
                         [{'status': 200}])

    def test_unavailable_backend_does_not_hide_healthy_vm_evidence(self):
        @contextlib.contextmanager
        def forward(resource,*args):
            if 'backend' in resource:
                raise RuntimeError('backend unavailable')
            yield 'http://vm'
        @contextlib.contextmanager
        def response(*args,**kwargs):
            class R:
                def read(self): return b'{"metric":{},"values":[],"timestamps":[]}\n'
            yield R()
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)
            with patch('evaluation.runner.kube_json',return_value={'items':[{'metadata':{'name':'victoria'}}]}), \
                 patch('evaluation.runner.forward',side_effect=forward), \
                 patch('evaluation.runner.urllib.request.urlopen',side_effect=response):
                export_window(Cell('boutique','trace',1),path,'2026-01-01T00:00:00Z','2026-01-01T00:03:00Z',180)
            self.assertTrue((path/'vm-raw.ndjson').exists())
            self.assertTrue((path/'api-export.error').exists())
