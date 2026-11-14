"""The public launch gate delegates only to the active attempt protocol."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from evaluation.gke import readiness
from evaluation.gke.models import RECOVERABLE_SCHEDULES


class LaunchDispatchTests(unittest.TestCase):
    def test_active_schedules_use_current_validator_and_propagate_failures(self):
        for schedule in RECOVERABLE_SCHEDULES:
            with self.subTest(schedule=schedule):
                ctx=SimpleNamespace(manifest={'schedule':schedule})
                with patch.object(readiness,'definition',side_effect=AssertionError('retired probe gate invoked')), \
                     patch('evaluation.gke.recoverable_run.validate_launch',side_effect=ValueError('current gate rejected input')) as validate:
                    with self.assertRaisesRegex(ValueError,'current gate rejected input'):
                        readiness.validate_launch(ctx)
                    validate.assert_called_once_with(ctx)

    def test_legacy_or_missing_schedule_is_rejected_before_active_validator(self):
        for schedule in (None,'qualification','diagnostic-rehearsal','thesis-qualification-v2','thesis-qualification-v3','unknown'):
            with self.subTest(schedule=schedule),patch('evaluation.gke.recoverable_run.validate_launch') as validate:
                with self.assertRaisesRegex(ValueError,'legacy schedules are analysis-only'):
                    readiness.validate_launch(SimpleNamespace(manifest={'schedule':schedule}))
                validate.assert_not_called()


if __name__=='__main__':unittest.main()
