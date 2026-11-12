import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from evaluation.gke import readiness, run
from evaluation.tests.test_gke_run import RunTests, FakeDriver
from evaluation.tests.test_gke_readiness import ReadinessTests


class ReadinessOnlyTests(unittest.TestCase):
    def test_passing_readiness_never_starts_measurement_and_still_cleans_up(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx=RunTests().context(directory)
            ctx.config['execution_scope']='readiness-only'
            driver=FakeDriver()
            report=run.execute_matrix(ctx,driver,gate=lambda ctx,driver:{'ready':True})
            self.assertEqual(driver.events,[('finish',)])
            self.assertEqual(report['executed_windows'],0)
            self.assertEqual(report['planned_windows'],0)
            self.assertEqual(report['planned_qualification_windows'],46)
            self.assertEqual(report['state'],'complete')
            self.assertFalse(report['qualified'])

    def test_failed_readiness_still_cleans_up_and_stays_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx=RunTests().context(directory)
            ctx.config['execution_scope']='readiness-only'
            driver=FakeDriver()
            report=run.execute_matrix(ctx,driver,gate=lambda ctx,driver:{'ready':False,'error':'capture failed'})
            self.assertEqual(driver.events,[('finish',)])
            self.assertEqual(report['executed_windows'],0)
            self.assertEqual(report['state'],'incomplete')

    def test_all_probes_and_reset_required_but_full_study_forecast_not_used(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx=ReadinessTests().context(Path(directory))
            ctx.config['execution_scope']='readiness-only'
            with patch.object(readiness,'forecast',side_effect=AssertionError('full study not authorized')):
                report=readiness.execute(ctx,
                    lambda spec:{'state':'passed','export':{'verified':True}},
                    lambda:{'valid':True},lambda path:{'verified':True},clock=lambda:1)
            self.assertTrue(report['ready'])
            self.assertEqual(report['counts']['passed'],13)
            self.assertTrue(report['reset']['valid'])


if __name__=='__main__':unittest.main()
