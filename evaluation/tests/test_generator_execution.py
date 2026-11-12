import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from evaluation.generator_execution import execute_generator, ArtifactExportError
from evaluation.tests.test_generator_artifacts import bundle


def job():
    return {'metadata':{'name':'load'},'spec':{'template':{'spec':{'containers':[
        {'name':'load','env':[{'name':'DURATION','value':'1s'}]}]}}}}


def pods(terminated=False):
    return {'items':[{'metadata':{'name':'load-pod'},'status':{'containerStatuses':[
        {'name':'load','state':{'terminated':{'exitCode':1}} if terminated else {'running':{}}}]}}]}


class GeneratorExecutionTests(unittest.TestCase):
    def run_case(self,path,*,corrupt=False,exit_code=0,terminated=False,study_seconds=None,readiness_timeout=False):
        operations=[]
        self.operations=operations
        pod_reads=0
        def kube(*args,**kwargs):
            nonlocal pod_reads
            operations.append(args)
            if args[:2]==('get','pods'):
                pod_reads += 1
                if readiness_timeout and pod_reads == 2:
                    raise subprocess.TimeoutExpired(args, kwargs['timeout'])
                text=json.dumps(pods(terminated))
            elif args[:2]==('get','job'):
                text=json.dumps({'status':{'failed':1} if exit_code else {'succeeded':1}})
            elif 'ACK' in ' '.join(args):
                self.assertTrue((path/'generator-artifact-status.json').exists())
                self.assertTrue((path/'generator-window.json').exists())
                self.assertTrue((path/'ledger.ndjson').exists())
                self.assertTrue((path/'k6-summary.json').exists())
                self.assertEqual(json.loads((path/'generator-artifact-status.json').read_text())['verified'],True)
                text=''
            else:
                text=''
            return subprocess.CompletedProcess(args,0,text,'')
        def download(ns,pod,container,destination,generator,mode,run_id,**kwargs):
            operations.append(('download',))
            self.export_timeout=kwargs.get('timeout_seconds')
            if corrupt: raise ValueError('checksum mismatch')
            bundle(destination,exit_code)
            from evaluation.generator_artifacts import validate_export
            return validate_export(destination,generator,mode,run_id)
        with patch('evaluation.generator_execution.r.apply',side_effect=lambda obj:operations.append(('apply',))),patch('evaluation.generator_execution.r.kube',side_effect=kube),patch('evaluation.generator_execution.download_export',side_effect=download),patch('evaluation.generator_execution.r._DEADLINE',None if study_seconds is None else time.monotonic()+study_seconds):
            result=execute_generator(job(),'ns',path,'run','k6','matched')
        return result,operations

    def test_transient_readiness_timeout_retains_artifacts_inside_original_budget(self):
        with tempfile.TemporaryDirectory() as d, patch('evaluation.generator_execution.time.sleep'):
            path = Path(d)
            result, operations = self.run_case(path, readiness_timeout=True, study_seconds=5)
            phases = [json.loads(line)['phase'] for line in
                      (path/'generator-execution-progress.ndjson').read_text().splitlines()]
            self.assertIn('ready_probe_timeout', phases)
            self.assertTrue(result['verified'])
            self.assertTrue(result['acknowledged'])
            self.assertEqual(operations.count(('apply',)), 1)

    def test_ack_follows_verified_raw_and_materialized_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            result,operations=self.run_case(Path(d))
            self.assertTrue(result['verified'])
            self.assertEqual(result['pod_name'],'load-pod')
            ack=next(i for i,op in enumerate(operations) if 'ACK' in ' '.join(op))
            self.assertLess(operations.index(('download',)),ack)
            self.assertFalse(result['kubernetes_job_failed'])
            self.assertFalse(any('delete' in op for op in operations))

    def test_corrupt_export_never_acknowledges_or_deletes(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            with self.assertRaisesRegex(ArtifactExportError,'checksum mismatch'):
                self.run_case(p,corrupt=True)
            status=json.loads((p/'generator-artifact-status.json').read_text())
            self.assertFalse(status['verified']); self.assertFalse(status['semantic_complete'])
            progress=(p/'generator-execution-progress.ndjson').read_text()
            self.assertNotIn('acknowledged',[json.loads(line)['phase'] for line in progress.splitlines()])
            self.assertFalse(any('ACK' in ' '.join(op) or 'delete' in op for op in self.operations))

    def test_workload99_and_failed_job_are_verified_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            result,_=self.run_case(Path(d),exit_code=99)
            self.assertEqual(result['workload_exit_code'],99)
            self.assertTrue(result['kubernetes_job_failed'])
            self.assertTrue(result['verified'])

    def test_terminated_before_ready_is_retained_as_failed_capture(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            with self.assertRaisesRegex(ArtifactExportError,'terminated before READY'):
                self.run_case(p,terminated=True)
            self.assertFalse(json.loads((p/'generator-artifact-status.json').read_text())['verified'])

    def test_materialization_failure_never_acknowledges_verified_download(self):
        with tempfile.TemporaryDirectory() as d,patch('evaluation.generator_execution._materialize',side_effect=OSError('local disk full')):
            p=Path(d)
            with self.assertRaisesRegex(ArtifactExportError,'local disk full'):
                self.run_case(p)
            self.assertTrue((p/'generator-artifacts/SHA256SUMS').exists())
            self.assertFalse(any('ACK' in ' '.join(op) or 'delete' in op for op in self.operations))
            self.assertFalse(json.loads((p/'generator-artifact-status.json').read_text())['semantic_complete'])

    def test_export_timeout_is_capped_by_remaining_study_time(self):
        with tempfile.TemporaryDirectory() as d:
            result,_=self.run_case(Path(d),study_seconds=5)
            self.assertTrue(result['verified'])
            self.assertGreater(self.export_timeout,0)
            self.assertLessEqual(self.export_timeout,5)

    def test_expired_study_deadline_does_not_apply_or_delete(self):
        with tempfile.TemporaryDirectory() as d,patch('evaluation.generator_execution.r._DEADLINE',0),patch('evaluation.generator_execution.r.apply') as apply,patch('evaluation.generator_execution.r.kube') as kube:
            p=Path(d)
            with self.assertRaises(ArtifactExportError):
                execute_generator(job(),'ns',p,'run','k6','matched')
            apply.assert_not_called();kube.assert_not_called()
            self.assertFalse(json.loads((p/'generator-artifact-status.json').read_text())['verified'])

if __name__=='__main__': unittest.main()
