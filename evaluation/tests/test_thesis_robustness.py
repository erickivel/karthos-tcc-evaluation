import unittest
from unittest.mock import patch

from evaluation import pilot
from evaluation.scoring import compare


class BoundedSleepTests(unittest.TestCase):
    def test_clock_crossing_end_never_sleeps_negative(self):
        with patch.object(pilot.time, 'monotonic', side_effect=[0, .9, 1.1, 1.2]), patch.object(pilot.r, '_DEADLINE', None), patch.object(pilot.time, 'sleep') as sleep:
            pilot.sleep_bounded(1)
        self.assertTrue(all(call.args[0] >= 0 for call in sleep.call_args_list))

    def test_sleep_does_not_overrun_study_deadline(self):
        with patch.object(pilot.time, 'monotonic', side_effect=[0, 1, 2]), patch.object(pilot.r, '_DEADLINE', 2), patch.object(pilot.time, 'sleep') as sleep:
            with self.assertRaises(TimeoutError):
                pilot.sleep_bounded(10)
        self.assertLessEqual(sleep.call_args_list[0].args[0], 1)


class ComparisonTests(unittest.TestCase):
    def test_absolute_relative_and_declared_tolerance(self):
        result = compare(100, 110, .15, 2)
        self.assertEqual(result.get('relative_error'), .1)
        self.assertEqual(result.get('relative_tolerance'), .15)
        self.assertEqual(result.get('absolute_tolerance'), 2)
        self.assertEqual(compare(0, 1, .15, 2).get('relative_error'), None)

    def test_nonfinite_reference_is_missing_evidence(self):
        for value in (float('nan'), float('inf')):
            self.assertEqual(compare(value, 1, .15, 2)['state'], 'incomplete')


class BaselineBreakdownTests(unittest.TestCase):
    def test_every_planned_baseline_and_invalid_attempt_is_visible(self):
        from evaluation.gke.baseline_results import baseline_results
        matrix = [dict(id=str(i), phase='baseline', cell='boutique-trace', repeat=i) for i in range(4)]
        windows = [dict(id='0', state='failed', attempt_id='a'), dict(id='0', state='complete', attempt_id='b'), dict(id='2', state='failed')]
        exported = {
            '0': ('gs://valid', {'families': {'traffic_count': {'state': 'pass', 'reference': 10}}}),
            '1': ('gs://disagreement', {'families': {'traffic_count': {'state': 'fail'}}}),
        }
        result = baseline_results(matrix, windows, exported, lambda d: d['families']['traffic_count']['state'])
        self.assertEqual(result['counts'], dict(planned=4, evaluable=2, passed=1, failed=1, insufficient=2, not_applicable=0))
        self.assertEqual(len(result['baselines'][0]['attempts']), 2)
        self.assertEqual(result['baselines'][2]['verdict'], 'insufficient')
        self.assertEqual(result['baselines'][3]['attempts'], [])
        self.assertEqual(result['baselines'][0]['families']['traffic_count']['reference'], 10)

    def test_no_baselines_cannot_pass(self):
        from evaluation.gke.baseline_results import baseline_results
        self.assertEqual(baseline_results([], [], {}, lambda d: 'pass')['verdict'], 'insufficient')
