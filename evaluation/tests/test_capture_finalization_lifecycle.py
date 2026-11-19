import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from evaluation.structured_capture import RepairCapture
from evaluation.timing_capture import AvailabilityCapture
from evaluation.protocol import Cell
from evaluation.sample_records import iter_records


def pod():
    return {'metadata':{'name':'collector','uid':'uid'},'spec':{'nodeName':'node'},
            'status':{'containerStatuses':[{'name':'gateway','restartCount':0}]}}


class FinalizationLifecycleTests(unittest.TestCase):
    def test_structured_freezes_once_and_records_source_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            capture=RepairCapture(SimpleNamespace(namespace='app'),Path(directory),time.time()+300,
                                  {'capture_finalization_version':2})
            capture.pods={'gateway':pod()}
            data=b'{"resourceMetrics":[]}\n'
            class Reader:
                def __init__(self,*args,**kwargs):pass
                def __enter__(self):return self
                def __exit__(self,*args):pass
                def __call__(self,offset):return {'inode':7,'size':len(data),'data':data[offset:],
                    'requested_at_ns':100,'received_at_ns':200}
            with patch('evaluation.capture_finalization.SnapshotReader',Reader), \
                 patch.object(capture,'_pod',return_value=pod()), patch.object(capture,'_get',return_value={'items':[]}), \
                 patch('evaluation.structured_capture.r.kube',return_value=SimpleNamespace(stdout='file\t/diag/metrics.json\nfile\t/diag/traces.json\n')):
                first=capture.freeze_snapshots()
                second=capture.freeze_snapshots()
            self.assertEqual(first,second)
            component=first['components']['gateway']
            self.assertEqual(component['collector_uid'],'uid')
            self.assertTrue(component['files']['metrics.json']['complete'])
            self.assertEqual((Path(component['raw_directory'])/'metrics.json').read_bytes(),data)

    def test_reader_close_failure_preserves_receipt_and_retained_byte_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            capture=RepairCapture(SimpleNamespace(namespace='app'),Path(directory),time.time()+300,
                                  {'capture_finalization_version':2})
            capture.pods={'gateway':pod()}
            data=b'{}\n'
            class Reader:
                def __init__(self,pod,name,deadline):self.name=name
                def __enter__(self):return self
                def __exit__(self,*args):
                    if self.name=='metrics.json':raise RuntimeError('reader close failed')
                def __call__(self,offset):return {'inode':7,'size':len(data),'data':data[offset:],
                    'requested_at_ns':100,'received_at_ns':200}
            with patch('evaluation.capture_finalization.SnapshotReader',Reader), \
                 patch('evaluation.structured_capture.MAX_BYTES',5), \
                 patch.object(capture,'_pod',return_value=pod()), patch.object(capture,'_get',return_value={'items':[]}), \
                 patch('evaluation.structured_capture.r.kube',return_value=SimpleNamespace(stdout='file\t/diag/metrics.json\nfile\t/diag/traces.json\n')):
                result=capture.freeze_snapshots()
            component=result['components']['gateway'];raw=Path(component['raw_directory'])
            self.assertFalse(result['complete'])
            self.assertTrue(component['files']['metrics.json']['complete'])
            self.assertEqual(component['files']['metrics.json']['sealed_bytes'],len(data))
            self.assertEqual((raw/'metrics.json').read_bytes(),data)
            self.assertFalse(component['files']['traces.json']['complete'])
            self.assertLessEqual(sum(file.stat().st_size for file in raw.iterdir()),5)
            self.assertTrue(any('reader close failed' in error for error in component['errors']))
            self.assertTrue(any('byte bound' in error for error in component['errors']))

    def test_timing_consumes_frozen_remainder_before_closing_streams(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);raw=root/'frozen';raw.mkdir();(raw/'metrics.json').write_bytes(b'{"a":1}\n')
            receipt={'complete':True,'cutoff_bytes':7,'sealed_bytes':8,'inode':7,
                     'cutoff_requested_at_ns':100,'cutoff_received_at_ns':110,'completed_at_ns':200,
                     'sha256':__import__('hashlib').sha256(b'{\"a\":1}\n').hexdigest()}
            repair=SimpleNamespace(pods={'gateway':pod()},settings={'capture_finalization_version':2},
                freeze_snapshots=lambda:{'components':{'gateway':{'collector_uid':'uid','raw_directory':str(raw),'files':{'metrics.json':receipt}}}})
            with patch('evaluation.gke.poll_capture.compiled_storage',side_effect=ValueError('fixture')):
                capture=AvailabilityCapture(Cell('boutique','trace',1),root,repair)
            capture._expected={'gateway:metrics.json'}
            capture._collector_streams={'gateway:metrics.json':('gateway',pod(),'metrics.json')}
            capture.status['polls']=[{}]
            capture._files={'gateway:metrics.json':{'inode':7,'offset':7,'carry':b'{"a":1}','backlog':0}}
            capture._thread=None
            result=capture.stop()
            self.assertTrue(result['valid'],result)
            self.assertEqual(capture._files['gateway:metrics.json']['carry'],b'')
            rows=list(iter_records(capture.writer.path))
            self.assertEqual(rows[-1]['body'],'{"a":1}\n')
            self.assertEqual(rows[-1]['phase'],'finalization')
            self.assertGreaterEqual(rows[-1]['received_at_ns'],200)
            self.assertEqual(capture.stop(),result)

    def test_unsupported_directory_and_symlink_are_not_hidden_by_inventory(self):
        for entry in ('unsupported\t/diag/nested', 'unsupported\t/diag/metrics.json'):
            with self.subTest(entry=entry),tempfile.TemporaryDirectory() as directory:
                capture=RepairCapture(SimpleNamespace(namespace='app'),Path(directory),time.time()+300,
                                      {'capture_finalization_version':2})
                capture.pods={'gateway':pod()}
                with patch.object(capture,'_pod',return_value=pod()), patch.object(capture,'_get',return_value={'items':[]}), \
                     patch('evaluation.structured_capture.r.kube',return_value=SimpleNamespace(stdout=entry+'\n')):
                    result=capture.freeze_snapshots()
                self.assertFalse(result['complete'])
                self.assertTrue(any('unexpected' in e for e in result['errors']),result)

    def test_config_reader_mounts_the_same_configmap_volume_read_only(self):
        from evaluation.structured_capture import instrumentation_plan
        from evaluation.tests.test_capture_v4 import objects
        cm,work=objects('gateway')
        cm['metadata']['name']='gateway-config'
        spec=work['spec']['template']['spec']
        spec['volumes']=[{'name':'cfg','configMap':{'name':'gateway-config'}}]
        spec['containers'][0]['volumeMounts']=[{'name':'cfg','mountPath':'/conf'}]
        plan=instrumentation_plan('gateway',cm,work,'owner',{'observer_image':'r@sha256:x','astronomy_locust_image':'p@sha256:x','capture_finalization_version':2})
        reader=next(c for c in plan['after_workload']['spec']['template']['spec']['containers'] if c['name']=='diag-reader')
        self.assertIn({'name':'cfg','mountPath':'/evaluation-config','readOnly':True},reader['volumeMounts'])
        self.assertEqual(plan['mounted_config_path'],'/evaluation-config/config.yaml')

    def test_mounted_settings_are_verified_before_traffic_and_mismatch_rejected(self):
        import hashlib
        config={'connectors':{'service_graph':{'store':{'max_items':10000,'ttl':'30s'},'metrics_flush_interval':'15s',
                    'latency_histogram_buckets':['100ms','250ms','500ms','1s','5s']}}}
        text=json.dumps(config)
        for actual,valid in ((text,True),(text.replace('10000','1000'),False)):
            with self.subTest(valid=valid),tempfile.TemporaryDirectory() as directory:
                capture=RepairCapture(Cell('boutique','trace',1),Path(directory),time.time()+300,
                                      {'capture_finalization_version':2,'servicegraph_store_max_items':10000})
                capture.pods={'gateway':pod()};capture.plans=[{'kind':'gateway','mounted_config_path':'/evaluation-config/config.yaml',
                    'mounted_config_volume':'cfg','key':'config.yaml','after_configmap':{'data':{'config.yaml':text}}}]
                with patch('evaluation.structured_capture.r.kube',return_value=SimpleNamespace(stdout=actual)), \
                     patch.object(capture,'_pod',return_value=pod()):
                    if not valid:
                        with self.assertRaisesRegex(ValueError,'mounted'):capture._verify_mounted_configs()
                    else:
                        capture._verify_mounted_configs()
                        proof=capture.state['instrumentation']['mounted_config_proof']
                        self.assertTrue(proof['valid'])
                        self.assertEqual(proof['receipts'][0]['servicegraph']['store_max_items'],10000)
                        self.assertEqual(proof['receipts'][0]['mounted_sha256'],hashlib.sha256(text.encode()).hexdigest())

class RetainedReceiptTests(unittest.TestCase):
    def test_archive_receipts_point_to_retained_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            capture=RepairCapture(Cell('boutique','trace',1),Path(directory),time.time()+300,{'capture_finalization_version':2})
            dest=capture._destination('gateway');dest.mkdir(parents=True,exist_ok=True)
            raw=dest/'raw-attempt-1';raw.mkdir();(raw/'metrics.json').write_text('{}\n')
            frozen={'raw_directory':str(raw),'files':{},'errors':[]}
            capture.frozen_snapshots={'components':{'gateway':frozen}};capture.state['finalization']=capture.frozen_snapshots
            (capture.root/'snapshot-finalization.json').write_text(json.dumps(capture.frozen_snapshots))
            result={'raw_directory':str(raw),'finalization':frozen,'files':[],'errors':[]}
            with patch.object(capture,'_archive_attempt',return_value=result):capture._archive('gateway')
            for receipt in (capture.frozen_snapshots,json.loads((capture.root/'snapshot-finalization.json').read_text())):
                self.assertEqual(receipt['components']['gateway']['raw_directory'],str(dest/'raw'))
            status=json.loads((dest/'archive-attempt-1-status.json').read_text())
            self.assertEqual(status['raw_directory'],str(dest/'raw'))
            self.assertTrue(Path(status['finalization']['raw_directory']).is_dir())

class AggregateSnapshotTests(unittest.TestCase):
    def test_directory_overflow_is_reported_without_discarding_originals(self):
        from evaluation.structured_capture import inspect_directory
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'metrics.json').write_bytes(b'{}\n');(root/'traces.json').write_bytes(b'{}\n')
            result=inspect_directory(root,max_bytes=5)
            self.assertEqual(result['total_bytes'],6)
            self.assertIn('capture aggregate byte bound exceeded',result['errors'])
            self.assertEqual(len(result['files']),2)
            self.assertEqual((root/'traces.json').read_bytes(),b'{}\n')
