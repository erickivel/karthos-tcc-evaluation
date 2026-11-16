import copy
import json
import tempfile
import unittest
from pathlib import Path
from evaluation.gke import compact_report as compact


class CompactReportTests(unittest.TestCase):
    def report(self):
        same={'value':2,'pod_uid':'one','acquisition_started_at':1}
        check={'state':'fail','observed':{'value':2,'pod_uid':'one'},
               'matching_references':[same,dict(same),dict(same,value=3)]}
        return {'schema_version':1,'execution':{'state':'complete'},
                'numerical_agreement':{'state':'fail','checks':[check,check]},
                'selected_accuracy':[{'name':'cpu','state':'fail','checks':[check]}]}

    def test_round_trip_preserves_occurrences_conflicts_and_reuses_payloads(self):
        report=self.report();before=copy.deepcopy(report)
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'gke-evaluation.json'
            summary=compact.write_report(target,report)
            self.assertEqual(report,before)
            self.assertEqual(compact.read_report(target,projection=False),before)
            files=summary['compact_report']['members']
            self.assertEqual(files['comparisons']['records'],1)
            self.assertEqual(files['references']['records'],2)
            self.assertEqual(len(summary['numerical_agreement']['checks']),2)
            self.assertNotIn('matching_references',target.read_text())
            self.assertEqual(list(compact.iter_comparisons(target)),[report['numerical_agreement']['checks'][0]])

    def test_projection_and_checksums_include_omitted_reference_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'gke-evaluation.json'
            root=compact.write_report(target,self.report())
            projected=compact.read_report(target)
            self.assertEqual(projected['numerical_agreement']['checks'][0]['state'],'fail')
            self.assertNotIn('matching_references',projected['numerical_agreement']['checks'][0])
            self.assertNotIn('checks',projected['selected_accuracy'][0])
            member=Path(directory)/root['compact_report']['members']['references']['name']
            member.write_bytes(member.read_bytes().replace(b'"value":2',b'"value":9'))
            with self.assertRaisesRegex(ValueError,'checksum'):
                compact.read_report(target)

    def test_failed_encoding_leaves_previous_report_and_members_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'gke-evaluation.json'
            compact.write_report(target,self.report());before={p.name:p.read_bytes() for p in Path(directory).iterdir()}
            with self.assertRaises(TypeError):compact.write_report(target,{'numerical_agreement':{'checks':[{'bad':object()}]}})
            self.assertEqual(before,{p.name:p.read_bytes() for p in Path(directory).iterdir()})

    def test_legacy_reports_still_read(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'gke-evaluation.json';target.write_text(json.dumps(self.report()))
            self.assertEqual(compact.read_report(target,projection=False),self.report())

    def test_offline_analysis_supports_small_compact_and_archive_callbacks(self):
        from evaluation.gke.analysis import _read_window_report
        from evaluation.gke.analysis_reader import read_analysis_report
        from evaluation.gke.models import sha
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'gke-evaluation.json'
            compact.write_report(target,self.report())
            result=_read_window_report(target,sha(target))
            self.assertEqual(result['numerical_agreement']['checks'][0]['state'],'fail')
            used={}
            with target.open('rb') as stream:
                archive_result,_=read_analysis_report(stream,sha(target),open_member=lambda name:(Path(directory)/name).open('rb'),input_members=used)
            self.assertEqual(result,archive_result)
            self.assertEqual(len(used),3)

    def test_projection_refuses_missing_external_members(self):
        from evaluation.gke.analysis_reader import read_analysis_report
        from evaluation.gke.models import sha
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'gke-evaluation.json';compact.write_report(target,self.report())
            with target.open('rb') as stream,self.assertRaisesRegex(ValueError,'external member'):
                read_analysis_report(stream,sha(target))

    def test_large_shared_reference_sets_are_not_repeated_per_comparison(self):
        refs=[{'identity':index,'value':index} for index in range(200)]
        checks=[{'observed':{'value':index},'matching_references':list(refs)} for index in range(20)]
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'gke-evaluation.json'
            root=compact.write_report(target,{'numerical_agreement':{'checks':checks}})
            members=root['compact_report']['members']
            self.assertEqual(members['reference_sets']['records'],1)
            self.assertEqual(members['comparisons']['records'],20)
            self.assertLess(members['comparisons']['bytes'],12000)
            self.assertEqual(compact.read_report(target,projection=False),{'numerical_agreement':{'checks':checks}})

    def test_writer_never_joins_whole_report_json(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'gke-evaluation.json'
            with patch('json.dumps',side_effect=AssertionError('monolithic encoding')):
                compact.write_report(target,self.report())
            self.assertEqual(compact.read_report(target,projection=False),self.report())

    def test_reserved_marker_attributes_round_trip_as_data(self):
        payload={'$compact_ref':{'kind':'comparisons','sha256':'x','offset':0,'bytes':1}}
        report={'attributes':payload,'nested':{'$compact_literal':[1,2]},
                'numerical_agreement':{'checks':[{'observed':{'attributes':payload},'matching_references':[payload]}]}}
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'gke-evaluation.json';compact.write_report(target,report)
            self.assertEqual(compact.read_report(target,projection=False),report)

    def test_oversized_record_fails_before_publication(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'gke-evaluation.json'
            with patch.object(compact,'MAX_RECORD_BYTES',64,create=True):
                with self.assertRaisesRegex(ValueError,'record.*bound'):
                    compact.write_report(target,{'numerical_agreement':{'checks':[{'observed':'x'*100}]}})
            self.assertFalse(target.exists());self.assertEqual(list(Path(directory).iterdir()),[])

    def test_comparison_iterator_does_not_decode_unrelated_root_traffic(self):
        from unittest.mock import patch
        report=self.report();report['traffic']={'large_retained_body':['unchanged']*1000}
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'gke-evaluation.json';compact.write_report(target,report)
            with patch('json.load',side_effect=AssertionError('whole root decode')):
                self.assertEqual(list(compact.iter_comparisons(target)),[report['numerical_agreement']['checks'][0]])

    def test_compact_analysis_equals_legacy_projection_values(self):
        import hashlib,io
        from evaluation.gke.analysis_reader import read_analysis_report
        report=self.report();report['traffic']={'report':{'availability':{'observations':[{'large':'body'}]},'keep':{'count':23}}}
        report['numerical_agreement']['checks'][0]['observed'].update(metric='system.cpu.time',unit='s',dimensions={'state':'idle'},timestamp=123.5)
        raw=json.dumps(report).encode();legacy,_=read_analysis_report(io.BytesIO(raw),hashlib.sha256(raw).hexdigest())
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'gke-evaluation.json';compact.write_report(target,report)
            self.assertEqual(compact.read_report(target),legacy)

    def test_privacy_projection_keeps_normal_refs_and_rejects_changed_payloads(self):
        from evaluation.gke.export import export_projection
        for sensitive in (False,True):
            with self.subTest(sensitive=sensitive),tempfile.TemporaryDirectory() as directory:
                source=Path(directory)/'source';target=Path(directory)/'projection';report=self.report()
                if sensitive:report['numerical_agreement']['checks'][0]['matching_references'][0]['password']='sensitive-value'
                compact.write_report(source/'gke-evaluation.json',report)
                export_projection(source,target)
                if sensitive:
                    with self.assertRaisesRegex(ValueError,'checksum'):compact.read_report(target/'gke-evaluation.json')
                else:self.assertEqual(compact.read_report(target/'gke-evaluation.json',projection=False),report)

    def test_comparison_iterator_rejects_truncated_root_after_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'gke-evaluation.json';compact.write_report(target,self.report())
            raw=target.read_bytes();target.write_bytes(raw[:-3])
            with self.assertRaises(ValueError):list(compact.iter_comparisons(target))
