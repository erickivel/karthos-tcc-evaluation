"""Exercise the named rehearsal without cloud access."""
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from evaluation.gke import models, run
from evaluation.tests import test_gke_run as fixtures


class RehearsalTests(unittest.TestCase):
    def context(self, directory):
        ctx = fixtures.RunTests().context(directory)
        ctx.manifest['schedule'] = 'diagnostic-rehearsal'
        ctx.manifest['matrix'] = models.build_matrix(ctx.manifest['protocol_definition'], schedule=ctx.manifest['schedule'])
        return ctx

    def test_order_one_cluster_and_fresh_restart_setups(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context(directory)
            driver = fixtures.FakeDriver(numeric='fail')
            report = run.execute_matrix(ctx, driver)
            rows = ctx.manifest['matrix']
            self.assertEqual([(r['phase'], r['cell'], r['generator'], r['scenario']) for r in rows], [
                ('smoke', 'astronomy-trace', 'k6', 'matched'),
                ('smoke', 'boutique-mesh', 'k6', 'matched'),
                ('smoke', 'boutique-mesh', 'locust', 'matched'),
                ('smoke', 'astronomy-mesh', 'locust', 'matched'),
                ('smoke', 'astronomy-mesh', 'k6', 'matched'),
                ('baseline', 'astronomy-trace', 'k6', 'matched'),
                ('baseline', 'boutique-mesh', 'locust', 'matched'),
                ('resource', 'boutique-trace', 'k6', 'cpu-memory'),
                ('resource', 'boutique-trace', 'k6', 'storage'),
                ('resource', 'boutique-trace', 'k6', 'network'),
                ('arbitration', 'boutique-mesh', 'k6', 'both-sources'),
                ('arbitration', 'boutique-trace', 'k6', 'replanned-trace'),
                ('restart', 'boutique-trace', 'k6', 'checkout'),
                ('restart', 'boutique-trace', 'k6', 'gateway'),
                ('restart', 'boutique-mesh', 'k6', 'checkout'),
                ('restart', 'boutique-mesh', 'k6', 'gateway'),
            ])
            self.assertEqual(sum(r['duration_seconds'] for r in rows), 3600)
            self.assertEqual([e for e in driver.events if e[0]=='repeat'], [('repeat', 1)])
            for row in rows[-4:]:
                position = driver.events.index(('window', row['id']))
                self.assertEqual(driver.events[position-1], ('setup', 1, row['cell']))
            self.assertEqual(report['state'], 'complete')
            self.assertEqual(report['planned_windows'], 16)
            self.assertFalse(report['qualified'])
            self.assertEqual(run.window_evidence_prefix(rows[-1], schedule=models.REHEARSAL),
                             'repeat-1/restarts/r1-restart-boutique-mesh-k6-gateway')

    def test_tampered_order_and_missing_smoke_rejected_before_setup(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context(directory)
            ctx.manifest['matrix'][0], ctx.manifest['matrix'][5] = ctx.manifest['matrix'][5], ctx.manifest['matrix'][0]
            driver = fixtures.FakeDriver()
            with self.assertRaises(ValueError):
                run.execute_matrix(ctx, driver)
            self.assertEqual(driver.events, [])

    def test_deadline_retains_unattempted_reasons(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context(directory)
            ctx.deadline = 1800
            driver = fixtures.FakeDriver()
            with patch.object(run.time, 'time', return_value=1):
                report = run.execute_matrix(ctx, driver)
            self.assertEqual(driver.events, [('finish',)])
            self.assertEqual(report['state'], 'incomplete')
            rows = json.loads((Path(directory)/'matrix.json').read_text())
            self.assertEqual(len(rows), 16)
            self.assertTrue(all(r['state']=='unattempted' and r['reason'] for r in rows))

    def test_unverified_export_stops_next_rehearsal_window(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context(directory)
            driver = fixtures.FakeDriver()
            driver.upload = lambda *args: {'verified': False}
            report = run.execute_matrix(ctx, driver)
            self.assertEqual(report['executed_windows'], 1)
            self.assertEqual(report['state'], 'incomplete')
            self.assertEqual(driver.events[-1], ('finish',))
