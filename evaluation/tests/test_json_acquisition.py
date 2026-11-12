import base64,hashlib,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from evaluation.structured_capture import FastCapture
from evaluation.sample_records import iter_records

class KubeletAcquisitionTests(unittest.TestCase):
    def result(self,body,stderr=b''):
        return SimpleNamespace(stdout=body,stderr=stderr,returncode=0)
    def summary(self,node='node-a'):
        return {'node':{'nodeName':node,'cpu':{'time':'2026-10-25T06:15:40Z','usageNanoCores':123}},'pods':[]}
    def sampler(self,directory):
        cap=FastCapture(SimpleNamespace(namespace='app'),Path(directory));cap.node='node-a';return cap
    def test_malformed_response_is_retained_and_retried_without_retiming_measurement(self):
        with tempfile.TemporaryDirectory() as directory:
            cap=self.sampler(directory);path=Path(directory)/'samples.ndjson.gz';valid=self.summary()
            with patch('evaluation.structured_capture.r.kube',side_effect=[self.result(b'{"node" broken}'),self.result(json.dumps(valid).encode())]) as kube:
                cap._sample_once('kubelet',path)
            rows=list(iter_records(path));self.assertEqual(len(rows),1);row=rows[0]
            self.assertIsNone(row['error']);self.assertEqual(row['summary'],valid)
            self.assertEqual(base64.b64decode(row['raw_response_base64']),json.dumps(valid).encode())
            self.assertEqual(kube.call_count,2);self.assertTrue(kube.call_args.kwargs['binary'])
            self.assertEqual(kube.call_args_list[0].kwargs['timeout'],5.)
            self.assertLessEqual(kube.call_args_list[1].kwargs['timeout'],1.)
            self.assertEqual(base64.b64decode(row['acquisition_attempts'][0]['stdout_base64']),b'{"node" broken}')
            self.assertEqual(row['requested_at_ns'],row['acquisition_attempts'][1]['requested_at_ns'])
            self.assertEqual(cap.sampling_errors,[])
    def test_normal_success_preserves_summary_and_hash_without_raw_duplication(self):
        with tempfile.TemporaryDirectory() as directory:
            cap=self.sampler(directory);path=Path(directory)/'samples.ndjson.gz';valid=self.summary();raw=json.dumps(valid).encode()
            with patch('evaluation.structured_capture.r.kube',return_value=self.result(raw)):
                cap._sample_once('kubelet',path)
            row=list(iter_records(path))[0]
            self.assertEqual(row['summary'],valid)
            self.assertEqual(row['acquisition_attempts'][0]['stdout_sha256'],hashlib.sha256(raw).hexdigest())
            self.assertNotIn('raw_response_base64',row)
            self.assertEqual(row['raw_response_retention'],'recovery-only')
            self.assertFalse(row['acquisition_recovered'])

    def test_persistent_malformed_json_remains_invalid_with_every_response(self):
        with tempfile.TemporaryDirectory() as directory:
            cap=self.sampler(directory);path=Path(directory)/'samples.ndjson.gz'
            with patch('evaluation.structured_capture.r.kube',return_value=self.result(b'{"node" broken}')) as kube:
                cap._sample_once('kubelet',path)
            row=list(iter_records(path))[0]
            self.assertEqual(kube.call_count,3);self.assertTrue(row['error']);self.assertNotIn('summary',row)
            self.assertEqual(len(row['acquisition_attempts']),3);self.assertTrue(cap.sampling_errors)
    def test_non_utf8_stderr_is_opaque_and_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            cap=self.sampler(directory);path=Path(directory)/'samples.ndjson.gz'
            with patch('evaluation.structured_capture.r.kube',return_value=self.result(json.dumps(self.summary()).encode(),b'\xfd')):
                cap._sample_once('kubelet',path)
            row=list(iter_records(path))[0];self.assertIsNone(row['error']);self.assertEqual(base64.b64decode(row['acquisition_attempts'][0]['stderr_base64']),b'\xfd')
    def test_wrong_source_node_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            cap=self.sampler(directory);path=Path(directory)/'samples.ndjson.gz'
            with patch('evaluation.structured_capture.r.kube',return_value=self.result(json.dumps(self.summary('other-node')).encode())):
                cap._sample_once('kubelet',path)
            self.assertTrue(list(iter_records(path))[0]['error']);self.assertTrue(cap.sampling_errors)
    def test_retry_does_not_extend_the_existing_one_second_cadence_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            cap=self.sampler(directory);path=Path(directory)/'samples.ndjson.gz'
            with patch('evaluation.structured_capture.time.monotonic',side_effect=[0.,0.,2.]),patch('evaluation.structured_capture.r.kube',return_value=self.result(b'{"node" broken}')) as kube:
                cap._sample_once('kubelet',path)
            self.assertEqual(kube.call_count,1);self.assertTrue(list(iter_records(path))[0]['error'])
