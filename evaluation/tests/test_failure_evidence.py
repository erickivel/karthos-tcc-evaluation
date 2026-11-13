import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from evaluation.gke.failure_evidence import retain_attempt_reports, copy_summary_diagnostics


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value))


class FailureEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.ctx=SimpleNamespace(result=self.root,write=write)
        self.window=self.root/'windows/a1';self.diagnostics=self.root/'attempt-diagnostics/a1'
        self.records=[{'id':'w','attempts':[{'attempt_id':'a1','path':str(self.window),'export':{'verified':False}}]}]
    def test_report_survives_when_raw_capture_is_not_stopped(self):
        write(self.window/'gke-evaluation.json',{'execution':{'state':'complete'},'evidence':{'valid':True}})
        write(self.window/'transition.json',{'safe':False})
        write(self.window/'structured/status.json',{'workers_stopped':False,'proxy_stopped':False})
        self.window.joinpath('structured/raw.log').write_text('never copy raw')
        report=retain_attempt_reports(self.ctx,self.records,self.root/'out')
        self.assertEqual(self.records[0]['attempts'][0]['path'],str(self.window))
        self.assertTrue((self.root/'out/attempt-reports/a1/gke-evaluation.json').exists())
        self.assertFalse((self.root/'out/attempt-reports/a1/structured/status.json').exists())
        self.assertFalse(list((self.root/'out').rglob('raw.log')))
        self.assertTrue(any(row.get('state')=='not_retained' for row in report['attempts'][0]['files']))
    def test_stopped_capture_status_retained(self):
        write(self.window/'structured/status.json',{'workers_stopped':True,'proxy_stopped':True,'valid':False})
        retain_attempt_reports(self.ctx,self.records,self.root/'out')
        self.assertTrue((self.root/'out/attempt-reports/a1/structured/status.json').exists())
    def test_corrupt_report_is_explicit(self):
        self.window.mkdir(parents=True);(self.window/'gke-evaluation.json').write_text('{')
        report=retain_attempt_reports(self.ctx,self.records,self.root/'out')
        rows=report['attempts'][0]['files']
        self.assertTrue(any(row['name']=='gke-evaluation.json' and row['state']=='not_retained' for row in rows))
    def test_verified_raw_export_does_not_duplicate_logs(self):
        write(self.diagnostics/'diagnostics-status.json',{'stopped':True})
        (self.diagnostics/'logs').mkdir();(self.diagnostics/'logs/c.log').write_text('large logs')
        self.records[0]['attempts'][0]['export']={'verified':True,'uri':'gs://evidence/window'}
        copy_summary_diagnostics(self.ctx,self.records,self.root/'summary')
        target=self.root/'summary/attempt-diagnostics/a1'
        self.assertTrue((target/'diagnostics-status.json').exists())
        self.assertTrue((target/'export-reference.json').exists())
        self.assertFalse((target/'logs').exists())
    def test_unexported_stopped_fatal_diagnostics_retained(self):
        write(self.diagnostics/'diagnostics-status.json',{'stopped':True})
        (self.diagnostics/'logs').mkdir();(self.diagnostics/'logs/c.log').write_text('fatal detail')
        copy_summary_diagnostics(self.ctx,self.records,self.root/'summary')
        self.assertTrue((self.root/'summary/attempt-diagnostics/a1/window.tar.gz').is_file())
        self.assertFalse((self.root/'summary/attempt-diagnostics/a1/logs/c.log').exists())
    def test_corrupt_or_live_diagnostics_are_excluded(self):
        self.diagnostics.mkdir(parents=True);(self.diagnostics/'diagnostics-status.json').write_text('{')
        (self.diagnostics/'live.log').write_text('mutable')
        copy_summary_diagnostics(self.ctx,self.records,self.root/'summary')
        self.assertFalse((self.root/'summary/attempt-diagnostics/a1/live.log').exists())
        markers=list((self.root/'summary').rglob('*unavailable.json'))
        self.assertEqual(len(markers),1)
    def test_orphan_stopped_fatal_diagnostics_also_retained(self):
        write(self.diagnostics/'diagnostics-status.json',{'stopped':True})
        (self.diagnostics/'failure.json').write_text('{"reason":"fatal before row"}')
        copy_summary_diagnostics(self.ctx,[],self.root/'summary')
        self.assertTrue((self.root/'summary/attempt-diagnostics/a1/window.tar.gz').is_file())

    def test_diagnostic_archive_hashes_and_gap_records(self):
        import tarfile,hashlib
        write(self.diagnostics/'diagnostics-status.json',{'stopped':True})
        (self.diagnostics/'logs').mkdir();(self.diagnostics/'logs/c.log').write_text('password=credential-fixture\nfatal detail')
        (self.diagnostics/'log-index.ndjson').write_text(json.dumps({'path':None,'coverage_gap':'instance byte budget exhausted'})+'\n')
        copy_summary_diagnostics(self.ctx,self.records,self.root/'summary')
        target=self.root/'summary/attempt-diagnostics/a1'
        index=json.loads((target/'diagnostic-archive-index.json').read_text())
        self.assertEqual(index['coverage_gaps'][0]['coverage_gap'],'instance byte budget exhausted')
        with tarfile.open(target/'window.tar.gz') as archive:
            for member in index['members']:
                raw=archive.extractfile(member['name']).read()
                self.assertEqual(hashlib.sha256(raw).hexdigest(),member['sha256'])
                self.assertEqual(len(raw),member['bytes'])
            self.assertNotIn(b'credential-fixture',archive.extractfile('logs/c.log').read())

    def test_rejected_diagnostic_archive_leaves_no_normal_archive(self):
        from unittest.mock import patch
        write(self.diagnostics/'diagnostics-status.json',{'stopped':True})
        (self.diagnostics/'logs').mkdir();(self.diagnostics/'logs/c.log').write_text('safe diagnostic')
        with patch('evaluation.gke.failure_evidence.verify_archive_privacy',side_effect=ValueError('inspection rejected')):
            result=copy_summary_diagnostics(self.ctx,self.records,self.root/'summary')
        self.assertEqual(result['attempts'][0]['state'],'not_retained')
        self.assertFalse(list((self.root/'summary').rglob('*.tar.gz')))
