import gzip
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from evaluation import structured_capture as capture
from evaluation import scoring


class SampleRecordsTests(unittest.TestCase):
    def test_production_samplers_use_lossless_compressed_paths(self):
        self.assertTrue(all(name.endswith('.ndjson.gz') for name in capture.FastCapture.FILES.values()))

    def test_compressed_records_remain_exact_and_readable_between_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            sampler = capture.FastCapture(SimpleNamespace(namespace='app'), Path(directory))
            path = Path(directory)/'istio-samples.ndjson.gz'
            for number in range(3):
                sampler._record(path, {'source':'mesh','text':'label="ação" 13\n'*10000,'sequence':number}, time.time_ns())
                records = scoring.rows(path.with_suffix(''))
                self.assertEqual(len(records),number+1)
                self.assertEqual(records[-1]['text'],'label="ação" 13\n'*10000)
                self.assertEqual(records[-1]['sequence'],number)
            self.assertLess(path.stat().st_size,10000)

    def test_legacy_and_compressed_candidates_are_ambiguous(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'samples.ndjson';p.write_text('{}\n')
            p.with_suffix('.ndjson.gz').write_bytes(gzip.compress(b'{}\n'))
            with self.assertRaisesRegex(ValueError,'ambiguous'):
                scoring.rows(p)

    def test_corrupt_or_truncated_gzip_never_becomes_empty_or_partial_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'samples.ndjson.gz'
            for payload in (b'',b'not gzip',gzip.compress(b'{"ok":1}\n')[:-3]):
                p.write_bytes(payload)
                with self.assertRaises((ValueError,EOFError,OSError)):
                    scoring.rows(p.with_suffix(''))

    def test_disk_limit_rejects_whole_record_without_damaging_previous_member(self):
        from evaluation.sample_records import BoundedRecords
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'samples.ndjson.gz'
            writer=BoundedRecords(p,200,10000)
            writer.append({'ok':1});before=p.read_bytes()
            with self.assertRaisesRegex(ValueError,'disk bound'):
                writer.append({'noise':list(range(1000))})
            self.assertEqual(p.read_bytes(),before)
            self.assertEqual(scoring.rows(p),[{'ok':1}])

    def test_logical_limit_remains_bounded_despite_high_compressibility(self):
        from evaluation.sample_records import BoundedRecords
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'samples.ndjson.gz';writer=BoundedRecords(p,10000,100)
            with self.assertRaisesRegex(ValueError,'uncompressed bound'):
                writer.append({'text':'a'*1000})
            self.assertFalse(p.exists())

    def test_removing_a_complete_gzip_member_is_detected_at_final_validation(self):
        from evaluation.sample_records import BoundedRecords
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'samples.ndjson.gz';writer=BoundedRecords(p,10000,10000)
            writer.append({'first':1});first=p.read_bytes()
            writer.append({'second':2});p.write_bytes(first)
            # The shortened file is valid gzip, but not a complete capture.
            self.assertEqual(scoring.rows(p),[{'first':1}])
            with self.assertRaisesRegex(ValueError,'integrity'):
                writer.validate()

    def test_writer_refuses_existing_evidence(self):
        from evaluation.sample_records import BoundedRecords
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'samples.ndjson.gz';p.write_bytes(b'original')
            with self.assertRaisesRegex(ValueError,'existing'):
                BoundedRecords(p,1000,10000)
            self.assertEqual(p.read_bytes(),b'original')

    def test_budgets_are_validated_before_cluster_changes(self):
        for key in ('capture_sample_max_bytes','capture_sample_max_uncompressed_bytes'):
            for value in (0,-1,True,'100'):
                with self.subTest(key=key,value=value),self.assertRaisesRegex(ValueError,'sampl'):
                    capture.RepairCapture(SimpleNamespace(namespace='app'),Path('/tmp/unused'),time.time()+600,config={key:value})

    def test_sampler_termination_records_time_and_last_success(self):
        with tempfile.TemporaryDirectory() as directory:
            sampler=capture.FastCapture(SimpleNamespace(namespace='app'),Path(directory))
            with patch.object(sampler,'_sample_once',side_effect=ValueError('independent sampling disk bound reached')):
                sampler._loop('mesh')
            status=json.loads((Path(directory)/'structured/sampling-status.json').read_text())
            self.assertFalse(status['valid'])
            self.assertIn('terminated_at',status['errors'][0])
            self.assertIn('disk bound',status['errors'][0]['error'])

    def test_preflight_rejects_overbudget_samples_and_retains_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            sampler=capture.FastCapture(SimpleNamespace(namespace='app'),Path(directory))
            sampler.sample_settings={'capture_expected_seconds':1000,
                                     'capture_sample_max_bytes':2000,
                                     'capture_sample_max_uncompressed_bytes':10000}
            def sample(stream,path):
                sampler._record(path,{'source':stream,'text':'x'*500},time.time_ns())
            with patch.object(sampler,'_sample_once',side_effect=sample):
                with self.assertRaisesRegex(RuntimeError,'preflight'):
                    sampler._sampling_preflight()
            status=json.loads((Path(directory)/'structured/sampling-status.json').read_text())
            self.assertFalse(status['valid'])
            self.assertFalse(status['projections']['mesh']['sufficient'])

    def test_preflight_checks_available_disk_reserve(self):
        with tempfile.TemporaryDirectory() as directory:
            sampler=capture.FastCapture(SimpleNamespace(namespace='app'),Path(directory))
            def sample(stream,path):
                sampler._record(path,{'source':stream,'text':'data'},time.time_ns())
            with patch.object(sampler,'_sample_once',side_effect=sample),patch('evaluation.structured_capture.shutil.disk_usage',return_value=SimpleNamespace(free=100)):
                with self.assertRaisesRegex(RuntimeError,'preflight'):
                    sampler._sampling_preflight()
            status=json.loads((Path(directory)/'structured/sampling-status.json').read_text())
            self.assertFalse(status['valid'])
            self.assertIn('disk',str(status['errors']))

    def test_status_write_failure_does_not_skip_archive_or_restoration(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);cap=capture.RepairCapture(SimpleNamespace(namespace='app'),path,time.time()+600)
            cap.capture=capture.FastCapture(SimpleNamespace(namespace='app'),path)
            cap.pods={'agent':{}}
            with patch.object(cap.capture,'stop'),patch.object(cap.capture,'_sampling_status',side_effect=OSError('disk unavailable')),patch.object(cap,'_check_source_collectors'),patch.object(cap,'_archive') as archive,patch.object(cap,'_restore',return_value={'restored':True}) as restore:
                result=cap.stop()
            archive.assert_called_once_with('agent');restore.assert_called_once()
            self.assertFalse(result['valid']);self.assertIn('disk unavailable',str(result['errors']))

    def test_capacity_projection_blocks_before_workload(self):
        from evaluation.sample_records import project_capacity
        row={'records':2,'stored_bytes':100,'uncompressed_bytes':1000}
        result=project_capacity(row,2,100,20000,200000)
        self.assertEqual(result['projected_stored_bytes'],10000)
        self.assertTrue(result['sufficient'])
        self.assertFalse(project_capacity(row,2,100,9999,200000)['sufficient'])


if __name__=='__main__':unittest.main()
