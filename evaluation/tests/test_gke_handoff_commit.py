import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory

from infrastructure.watchdog.main import effective_deadline
from evaluation.gke.readiness import forecast


class HandoffCommitTests(unittest.TestCase):
    def setUp(self):
        self.record = dict(run_id='current', started_at=1000, deadline=8200,
                           lease={'token': 'run-token'},
                           readiness_handoff={'study_deadline': 44200, 'commit_before': 7000})
        self.inventory = 'control/current/inventory/'+'a'*64+'.json'
        self.commit = dict(run_id='current', lease_token='run-token', inventory_object=self.inventory,
                           inventory_generation='123', study_deadline=44200)
        self.cloud = Mock()

    def test_pending_unreadable_late_and_mismatched_commits_keep_readiness_deadline(self):
        for observation in (None, (7000, self.commit), (6999, self.commit | {'run_id': 'old'}),
                            (6999, self.commit | {'lease_token': 'old'}),
                            (6999, self.commit | {'inventory_generation': '999'}),
                            (6999, self.commit | {'inventory_object': 'control/current/inventory/other.json'}),
                            (6999, self.commit | {'study_deadline': 50000})):
            with self.subTest(observation=observation):
                self.cloud.read_handoff.return_value = observation
                self.assertEqual(effective_deadline(self.record, self.cloud, '123', 7100, inventory_object=self.inventory), 8200)
        self.cloud.read_handoff.side_effect = OSError('storage unavailable')
        self.assertEqual(effective_deadline(self.record, self.cloud, '123', 7100, inventory_object=self.inventory), 8200)

    def test_timely_commit_survives_controller_exit_and_inventory_refresh(self):
        self.cloud.read_handoff.return_value = (6999, self.commit)
        self.assertEqual(effective_deadline(self.record, self.cloud, '123', 9000, inventory_object=self.inventory), 44200)
        self.record['readiness_handoff'].update(inventory_generation='123', inventory_object=self.inventory)
        self.assertEqual(effective_deadline(self.record, self.cloud, '124', 9000, inventory_object='control/current/inventory/'+'b'*64+'.json'), 44200)

    def test_forecast_does_not_add_setup_twice_to_archived_window_floor(self):
        ctx = SimpleNamespace(manifest={'matrix': [
            {'duration_seconds': 60, 'repeat': 1, 'cell': 'trace'},
            {'duration_seconds': 300, 'repeat': 1, 'cell': 'trace'},
        ]}, config={'limits': {'cleanup_reserve_seconds': 1800}})
        rows = [
            {'duration_seconds': 60, 'forecast_seconds': 1200,
             'archived_total_seconds': 1600, 'setup_seconds': 300},
            {'duration_seconds': 300, 'forecast_seconds': 1800,
             'archived_total_seconds': 2000, 'setup_seconds': 300},
        ]
        self.assertEqual(forecast(ctx, rows), 1600 + 2000 + 1800)
        rows[0]['forecast_seconds'] = 1400
        self.assertEqual(forecast(ctx, rows), 1700 + 2000 + 1800)

    def test_interrupted_or_expired_preparation_cannot_publish_commit(self):
        from evaluation.gke import provision

        for interrupted in (True, False):
            with self.subTest(interrupted=interrupted), TemporaryDirectory() as work:
                ctx = SimpleNamespace(run_id='current', deadline=44200, work=Path(work),
                                      state={'started_at':1000, 'inventory':{'generation':'123','object':self.inventory}})
                with patch.object(provision, 'arm_watchdog') as arm, patch.object(provision, 'upload_file') as upload:
                    arm.return_value = {'effective_deadline':11800}
                    if interrupted:
                        arm.side_effect = KeyboardInterrupt
                    with self.assertRaises(KeyboardInterrupt if interrupted else TimeoutError):
                        provision.commit_readiness_handoff(ctx, 7000, clock=Mock(side_effect=[6000, 7000]))
                    upload.assert_not_called()
                    self.assertNotIn('readiness_handoff_at', ctx.state)

    def test_late_storage_commit_is_not_accepted_by_controller(self):
        from evaluation.gke import provision

        with TemporaryDirectory() as work:
            ctx = SimpleNamespace(run_id='current', deadline=44200, work=Path(work),
                                  state={'started_at':1000, 'inventory':{'generation':'123','object':self.inventory},
                                         'lease':{'token':'run-token'}})
            receipt = {'uri':'gs://evidence/commit', 'generation':'456'}
            with patch.object(provision, 'arm_watchdog', return_value={'effective_deadline':11800}), \
                 patch.object(provision, 'upload_file', return_value=receipt), \
                 patch.object(provision, 'doc', return_value={'timeCreated':'1970-01-01T01:56:40Z','generation':'456'}):
                with self.assertRaises(TimeoutError):
                    provision.commit_readiness_handoff(ctx, 7000, clock=lambda:6000)
            self.assertNotIn('readiness_handoff_at', ctx.state)

    def test_watchdog_reads_the_exact_commit_generation(self):
        from infrastructure.watchdog.main import GCPCloud

        session = Mock()
        metadata, content = Mock(status_code=200), Mock(status_code=200)
        metadata.json.return_value = {'generation':'456','size':'200',
                                      'timeCreated':'1970-01-01T01:56:39Z'}
        content.json.return_value = self.commit
        session.get.side_effect = [metadata, content]
        with patch.dict('os.environ', {'INVENTORY_BUCKET':'evidence'}):
            observed = GCPCloud(session).read_handoff('current')
        self.assertEqual(observed, (6999, self.commit))
        first, second = session.get.call_args_list
        self.assertTrue(first.args[0].endswith('control%2Fcurrent%2Freadiness-handoff.json'))
        self.assertEqual(second.kwargs['params'], {'alt':'media','generation':'456'})
