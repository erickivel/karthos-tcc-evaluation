import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from evaluation import timing_pilot
from evaluation.gke.run import LiveDriver


class QueryPreparationTests(unittest.TestCase):
    def test_unknown_generator_never_falls_back_to_locust(self):
        with self.assertRaisesRegex(ValueError, 'unsupported load generator'):
            timing_pilot.make_job(None, {'generator': 'typo'}, 'run')

    def test_compiler_preparation_precedes_watchdog_and_failure_blocks_setup(self):
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                ctx=SimpleNamespace(result=root, deadline=time.time()+300,
                                    config={'limits':{'cleanup_reserve_seconds':30}})
                driver=LiveDriver(ctx)
                spec={'demo':'boutique','source':'trace','repeat':1,'phase':'smoke','scenario':'matched'}
                order=[]
                def compile_query(ctx,path,deadline):
                    order.append('compile')
                    self.assertEqual(path,root/'setup'/'query-compiler-preparation.json')
                    self.assertGreater(deadline,time.monotonic())
                    self.assertLessEqual(deadline,time.monotonic()+120)
                    if fail:raise RuntimeError('cold compiler failed')
                with patch('evaluation.gke.setup.setup_cell',return_value=root/'setup'), \
                     patch('evaluation.gke.poll_capture.prewarm',side_effect=compile_query), \
                     patch('evaluation.gke.provision.arm_watchdog',side_effect=lambda _:order.append('watchdog')):
                    if fail:
                        with self.assertRaisesRegex(RuntimeError,'cold compiler failed'):
                            driver.setup(spec)
                        self.assertIsNone(driver.cell)
                        self.assertEqual(order,['compile'])
                    else:
                        driver.setup(spec)
                        self.assertEqual(order,['compile','watchdog'])
                        self.assertEqual(driver.cell.demo,'boutique')
