import json
import tempfile
import unittest
from pathlib import Path
from evaluation.gke.controller_progress import collect
from evaluation.gke.recoverable_run import attempt_record

class FullRunFailureTests(unittest.TestCase):
    def test_large_report_progress_is_read_without_loading_nested_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); p=root/'r';p.mkdir()
            (p/'matrix.json').write_text('[]')
            (p/'progress.ndjson').write_text('{"at":1,"phase":"execution-finished"}\n')
            (p/'run-report.json').write_text(json.dumps({"state":"complete","failure":None,"evidence":"x"*(9*1024*1024)}))
            result=collect('r',base=root)
            self.assertEqual(result['report_state'],'complete',result)
            self.assertEqual(result['terminal_state'],'unknown')
            self.assertTrue(result['keep_observing'])

    def test_nested_capture_error_is_an_explicit_attempt_reason(self):
        report={"execution":{"state":"complete"},"evidence":{"valid":False,
            "agent_identities":{"valid":False,"errors":["identity lookup timed out"]}}}
        row=attempt_record({"id":"x-a1","logical_id":"x"},report)
        self.assertIn("identity lookup timed out",str(row['reason']))

    def test_invalid_capture_without_diagnostics_never_has_null_reason(self):
        row=attempt_record({"id":"x-a1","logical_id":"x"},{"execution":{"state":"complete"},"evidence":{"valid":False}})
        self.assertIsInstance(row['reason'],str)
        self.assertIn("evidence",row['reason'])

    def test_numerical_disagreement_is_not_execution_failure(self):
        row=attempt_record({"id":"x-a1","logical_id":"x"},{"execution":{"state":"complete"},"evidence":{"valid":True},"numerical_agreement":{"state":"fail"}})
        self.assertEqual(row['state'],'complete')
        self.assertIsNone(row['reason'])

class FinalizationIdentityTests(unittest.TestCase):
    def test_agent_inventory_is_shared_per_boundary_not_queried_per_node(self):
        import time
        from types import SimpleNamespace
        from unittest.mock import patch
        from evaluation.structured_capture import RepairCapture
        from evaluation.tests.test_capture_finalization_lifecycle import pod
        with tempfile.TemporaryDirectory() as d:
            capture=RepairCapture(SimpleNamespace(namespace='app'),Path(d),time.time()+300,{'capture_finalization_version':2})
            capture.pods={}
            for i in range(3):
                item=pod();item['metadata']['uid']='uid'+str(i);item['spec']['nodeName']='node'+str(i)
                capture.pods['agent:node'+str(i)]=item
            class Reader:
                def __init__(self,*args):pass
                def __enter__(self):return self
                def __exit__(self,*args):pass
                def __call__(self,offset):return {'inode':7,'size':3,'data':b'{}\n'[offset:],'requested_at_ns':100,'received_at_ns':200}
            with patch.object(capture,'_pods',return_value=capture.pods) as lookup, patch.object(capture,'_get',return_value={'items':[]}), \
                 patch('evaluation.capture_finalization.SnapshotReader',Reader), \
                 patch('evaluation.structured_capture.r.kube',return_value=SimpleNamespace(stdout='file\t/diag/metrics.json\nfile\t/diag/receiver-input.ndjson\n')):
                result=capture.freeze_snapshots()
            self.assertTrue(result['complete'],result)
            self.assertEqual(lookup.call_count,2)
            self.assertIn('identity_observations',result)

class PartialAgentTests(unittest.TestCase):
    def test_known_incomplete_receiver_is_not_parsed_and_original_cause_survives(self):
        from evaluation.evidence_v4 import agent_evidence
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);agent=root/'structured/agent/n';(agent/'raw').mkdir(parents=True)
            def write(p,v):p.write_text(json.dumps(v))
            write(root/'structured/nodes-before.json',{'items':[{'metadata':{'name':'n','uid':'node-uid'}}]})
            write(root/'structured/status.json',{'components':{'agent:n':{
                'collector_uid':'agent-uid','node_name':'n','errors':['receiver-input.ndjson: snapshot read deadline exhausted']}}})
            write(agent/'pod.json',{'metadata':{'uid':'agent-uid'},'spec':{'nodeName':'n'}})
            (agent/'raw/metrics.json').write_text('{"resourceMetrics":[]}\n')
            (agent/'raw/receiver-input.ndjson').write_text('{"body":"unfinished')
            result=agent_evidence(root)
            self.assertFalse(result['valid'])
            self.assertIn('snapshot read deadline exhausted','; '.join(result['errors']))
            self.assertNotIn('identity or lifecycle invalid','; '.join(result['errors']))

class MeshDiagnosisTests(unittest.TestCase):
    def test_timeout_retains_original_transport_evidence(self):
        import subprocess
        from types import SimpleNamespace
        from unittest.mock import patch
        from evaluation.structured_capture import FastCapture
        with tempfile.TemporaryDirectory() as d:
            capture=FastCapture(SimpleNamespace(source='mesh',namespace='app'),Path(d))
            recorded=[]
            error=subprocess.TimeoutExpired(['kubectl','get'],5,stderr=b'TLS handshake timeout')
            with patch('evaluation.structured_capture.r.kube',side_effect=error),patch.object(capture,'_record',side_effect=lambda p,row,t:recorded.append(row)):
                capture._sample_once('mesh',Path(d)/'mesh.ndjson',[('app','frontend')])
            self.assertEqual(recorded[0]['transport_diagnostic']['error_type'],'TimeoutExpired')
            self.assertEqual(recorded[0]['transport_diagnostic']['timeout_seconds'],5)
            self.assertIn('TLS handshake',recorded[0]['transport_diagnostic']['stderr'])
            self.assertTrue(recorded[0]['error'])

class ConcurrentFinalizationTests(unittest.TestCase):
    def test_independent_files_share_bounded_parallel_transfer_pool(self):
        import time,threading
        from types import SimpleNamespace
        from unittest.mock import patch
        from evaluation.structured_capture import RepairCapture
        from evaluation.tests.test_capture_finalization_lifecycle import pod
        barrier=threading.Barrier(2)
        class Reader:
            def __init__(self,*args):pass
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def __call__(self,offset):
                barrier.wait(timeout=1)
                return {'inode':7,'size':3,'data':b'{}\n','requested_at_ns':100,'received_at_ns':200}
        with tempfile.TemporaryDirectory() as d:
            capture=RepairCapture(SimpleNamespace(namespace='app'),Path(d),time.time()+300,{'capture_finalization_version':2})
            capture.pods={'gateway':pod()}
            with patch.object(capture,'_pod',return_value=pod()), patch.object(capture,'_get',return_value={'items':[]}), \
                 patch('evaluation.capture_finalization.SnapshotReader',Reader), \
                 patch('evaluation.structured_capture.r.kube',return_value=SimpleNamespace(stdout='file\t/diag/metrics.json\nfile\t/diag/traces.json\n')):
                result=capture.freeze_snapshots()
            self.assertTrue(result['complete'],result)

class StreamedReportTests(unittest.TestCase):
    def test_failure_with_fractional_timing_remains_json_serializable(self):
        from evaluation.gke.controller_progress import _report_status
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'report.json';p.write_text('{"state":"incomplete","failure":{"elapsed":0.125}}')
            self.assertIn('0.125',json.dumps(_report_status(p)))
    def test_non_object_report_is_rejected(self):
        from evaluation.gke.controller_progress import _report_status
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'report.json';p.write_text('[]')
            with self.assertRaises(ValueError):_report_status(p)
