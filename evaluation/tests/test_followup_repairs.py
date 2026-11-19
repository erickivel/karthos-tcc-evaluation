import json,tempfile,time,unittest,hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from evaluation.gke.controller_progress import collect,_report_status
from evaluation.structured_capture import RepairCapture,OWNER
from evaluation.tests.test_capture_finalization_lifecycle import pod

class MonitorTests(unittest.TestCase):
    def test_truncated_report_is_unknown_without_losing_counts(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'r';p.mkdir()
            (p/'matrix.json').write_text('[{"id":"x","state":"complete"}]')
            (p/'run-report.json').write_text('{"state":"complete","windows":[')
            (p/'progress.ndjson').write_text('{"at":1,"phase":"export"}\n')
            result=collect('r',base=root)
            self.assertEqual(result['completed_windows'],1)
            self.assertEqual(result['report_state'],'unknown')
            self.assertIn('report',result['errors'])
    def test_report_is_read_through_one_open_file(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'report.json';p.write_text('{"state":"complete","failure":null}')
            original=Path.open;opened=[]
            def opening(path,*a,**kw):
                if path==p:opened.append(path)
                return original(path,*a,**kw)
            with patch.object(Path,'open',opening):result=_report_status(p)
            self.assertEqual(result,{'state':'complete','failure':None})
            self.assertEqual(len(opened),1)

class SnapshotTests(unittest.TestCase):
    def test_sealed_snapshot_is_retained_if_late_inventory_fails(self):
        with tempfile.TemporaryDirectory() as d:
            cap=RepairCapture(SimpleNamespace(namespace='app'),Path(d),time.time()+300,{'capture_finalization_version':2})
            cap.pods={'gateway':pod()};dest=cap._destination('gateway');raw=dest/'raw-attempt-1';raw.mkdir(parents=True)
            (raw/'metrics.json').write_text('{"resourceMetrics":[]}\n');(raw/'traces.json').write_text('{"resourceSpans":[]}\n')
            cap.frozen_snapshots={'components':{'gateway':{'complete':True,'collector_uid':'uid','node_name':'node','raw_directory':str(raw),'files':{},'errors':[]}},'complete':True}
            with patch.object(cap,'_pod',side_effect=TimeoutError('late inventory')),patch.object(cap,'_get',side_effect=TimeoutError('late inventory')):
                cap._archive('gateway')
            self.assertTrue((dest/'raw/metrics.json').exists())
            self.assertIn('finalization',cap.state['components']['gateway'])
            self.assertNotIn('all archive attempts failed',cap.state['components']['gateway']['errors'])

    def test_one_inventory_per_boundary_includes_both_kinds(self):
        with tempfile.TemporaryDirectory() as d:
            cap=RepairCapture(SimpleNamespace(namespace='app'),Path(d),time.time()+300,{'capture_finalization_version':2})
            cap.pods={}
            for kind in ('gateway','agent'):
                item=pod();item['metadata'].update(name='karthos-'+kind+'-x',uid=kind,annotations={OWNER:cap.owner})
                item['status'].update(phase='Running',conditions=[{'type':'Ready','status':'True'}])
                cap.pods[kind if kind=='gateway' else 'agent:node']=item
            cap.nodes=['node']
            class Reader:
                def __init__(self,*a):pass
                def __enter__(self):return self
                def __exit__(self,*a):pass
                def __call__(self,offset):return {'inode':7,'size':3,'data':b'{}\n','requested_at_ns':1,'received_at_ns':2}
            with patch.object(cap,'_get',return_value={'items':list(cap.pods.values())}) as get,patch('evaluation.capture_finalization.SnapshotReader',Reader),patch('evaluation.structured_capture.r.kube',return_value=SimpleNamespace(stdout='file\t/diag/metrics.json\nfile\t/diag/traces.json\nfile\t/diag/receiver-input.ndjson\n')):
                result=cap.freeze_snapshots()
            self.assertTrue(result['complete'],result)
            self.assertEqual(get.call_count,2)
            for stage in ('before','after'):
                inventory=Path(d)/'structured'/('finalization-pods-'+stage+'.json')
                self.assertEqual(len(json.loads(inventory.read_text())['items']),2)

class MeshTransportTests(unittest.TestCase):
    def test_mesh_request_reports_each_failure_stage(self):
        from unittest.mock import MagicMock
        from evaluation import kubelet_transport as transport
        for stage in ('connect','request','headers','body'):
            with self.subTest(stage=stage):
                connection=MagicMock()
                response=connection.getresponse.return_value.__enter__.return_value
                response.read1.side_effect=[b'metric 1\n',b''];response.status=200
                failure=TimeoutError('fixture '+stage)
                {'connect':connection.connect,'request':connection.request,'headers':connection.getresponse,'body':response.read1}[stage].side_effect=failure
                with patch.object(transport,'UnixHTTPConnection',return_value=connection):
                    with self.assertRaises(TimeoutError) as raised:transport.mesh_request('/private/socket','app','frontend',5)
                self.assertIs(raised.exception,failure)
                self.assertEqual(raised.exception.transport_diagnostic['failed_stage'],stage)
                connection.close.assert_called_once()
    def test_mesh_success_preserves_bytes_and_exact_endpoint(self):
        from unittest.mock import MagicMock
        from evaluation import kubelet_transport as transport
        connection=MagicMock();response=connection.getresponse.return_value.__enter__.return_value
        response.read1.side_effect=[b'metric 1\n',b''];response.status=200
        with patch.object(transport,'UnixHTTPConnection',return_value=connection):
            result=transport.mesh_request('/private/socket','app','frontend',5)
        self.assertEqual(result.stdout,b'metric 1\n')
        self.assertEqual(connection.request.call_args.args[:2],('GET','/api/v1/namespaces/app/pods/frontend:15090/proxy/stats/prometheus'))
        self.assertEqual(set(result.transport_diagnostic['stages']),{'connect','request','headers','body'})
    def test_mesh_sampler_retains_detailed_failure(self):
        from evaluation.structured_capture import FastCapture
        from evaluation import kubelet_transport as transport
        with tempfile.TemporaryDirectory() as d:
            cap=FastCapture(SimpleNamespace(namespace='app',source='mesh'),Path(d));cap.mesh_socket='/socket'
            error=TimeoutError('headers timeout');error.transport_diagnostic={'failed_stage':'headers','stages':{'connect':0.1}}
            output=[]
            with patch.object(transport,'mesh_request',side_effect=error),patch.object(cap,'_record',side_effect=lambda p,row,t:output.append(row)):
                cap._sample_once('mesh',Path(d)/'mesh',[('app','frontend')])
            self.assertEqual(output[0]['transport_diagnostic']['failed_stage'],'headers')
