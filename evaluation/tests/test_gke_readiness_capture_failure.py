import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from evaluation.gke.readiness import UnsafeReadiness
from evaluation.gke.readiness_live import ProbeDriver
from evaluation.gke.models import read, write


class ReadinessFailureEvidenceTests(unittest.TestCase):
    def driver(self, errors=(), logs=(), alive=True):
        fast=SimpleNamespace(sampling_errors=list(errors),log_captures=list(logs),
            sampler_threads=[SimpleNamespace(name='kubelet-app-a',ident=12,is_alive=lambda:alive)],
            _sampling_status=lambda:{'streams':{'kubelet-app-a':{'records':3,'last_received_at':'fixture'}}})
        driver=ProbeDriver.__new__(ProbeDriver)
        driver.capture=SimpleNamespace(capture=fast)
        driver.ctx=SimpleNamespace(write=write)
        return driver

    def test_sampler_failure_is_written_before_gate_raises(self):
        error={'source':'kubelet-app-a','pod':'observer-a','error':'fixture read timeout'}
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);driver=self.driver(errors=[error])
            with self.assertRaises(UnsafeReadiness):driver._sampler_health({'id':'fixture','phase':'smoke'},{},path)
            self.assertTrue((path/'capture-health.json').is_file(),'failure diagnostics lost before raise')
            report=read(path/'capture-health.json')
            self.assertFalse(report['valid'])
            self.assertEqual(report['sampling_errors'],[error])
            self.assertEqual(report['streams']['kubelet-app-a']['records'],3)

    def test_failed_log_records_exact_pod_and_error(self):
        poller=SimpleNamespace(namespace='demo',pod='frontend-a',container='server',
            status=lambda:{'errors':['fixture log timeout'],'exited_before_stop':False,'bytes_written':120})
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);driver=self.driver(logs=[poller])
            with self.assertRaises(UnsafeReadiness):driver._sampler_health({'id':'fixture','phase':'smoke'},{},path)
            self.assertTrue((path/'capture-health.json').exists(),'log failure was not exported')
            report=read(path/'capture-health.json')
            self.assertEqual(report['unhealthy_logs'][0]['pod'],'frontend-a')
            self.assertEqual(report['unhealthy_logs'][0]['errors'],['fixture log timeout'])

    def test_dead_thread_is_distinguished_from_sampler_error(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);driver=self.driver(alive=False)
            with self.assertRaises(UnsafeReadiness):driver._sampler_health({'id':'fixture','phase':'smoke'},{},path)
            self.assertTrue((path/'capture-health.json').exists())
            report=read(path/'capture-health.json')
            self.assertEqual(report['sampling_errors'],[])
            self.assertEqual(report['sampler_threads'][0]['name'],'kubelet-app-a')
            self.assertFalse(report['sampler_threads'][0]['alive'])

    def test_healthy_sampler_still_passes_with_full_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);driver=self.driver()
            driver._sampler_health({'id':'fixture','phase':'smoke'},{},path)
            report=read(path/'capture-health.json')
            self.assertTrue(report['valid'])
            self.assertTrue(report['sampler_threads'][0]['alive'])
            self.assertEqual(report['unhealthy_logs'],[])

    def test_expected_restart_is_retained_but_does_not_invalidate_recovery(self):
        error={'source':'logs','pod':'old-checkout','error':'expected pod deletion'}
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);driver=self.driver(errors=[error])
            driver._sampler_health({'id':'fixture','phase':'restart'},
                {'pods_before':{'items':[{'metadata':{'name':'old-checkout'}}]}},path)
            report=read(path/'capture-health.json')
            self.assertTrue(report['valid'])
            self.assertEqual(report['sampling_errors'],[error])
            self.assertEqual(report['unexpected_sampling_errors'],[])

    def test_failed_case_compact_export_contains_capture_diagnostics(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);driver=self.driver(errors=[{'source':'mesh','error':'fixture timeout'}])
            driver.root=root;driver.ctx.run_id='fixture-run'
            driver.publish=lambda path,relative:{'verified':True}
            spec={'id':'fixture','phase':'smoke','scenario':'matched'}
            path=root/'cases/fixture';path.mkdir(parents=True)
            with self.assertRaises(UnsafeReadiness):driver._sampler_health(spec,{},path)
            result=driver.publish_case(spec,{'state':'failed','unsafe':True,'reason':'capture failed'})
            self.assertTrue(result['verified'])
            receipt=read(root/'receipts/fixture/receipt.json')
            self.assertFalse(receipt['evidence']['capture-health.json']['valid'])
            self.assertEqual(receipt['evidence']['capture-health.json']['sampling_errors'][0]['error'],'fixture timeout')
