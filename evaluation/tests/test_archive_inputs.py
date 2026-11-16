import io,json,tarfile,tempfile,unittest
from pathlib import Path
from evaluation.gke.models import sha,write
from evaluation.gke.export import seal
from evaluation.gke.compact_report import write_report
from evaluation.gke import archive_inputs


class ArchiveInputsTests(unittest.TestCase):
    def bundle(self,directory,target):
        seal(directory)
        with tarfile.open(target,'w:gz') as tar:
            for p in directory.iterdir():tar.add(p,arcname=p.name)
        return sha(target)

    def fixtures(self,root,extra=None):
        raw=root/'raw';raw.mkdir();write(raw/'gke-evaluation.json',{'analysis':{'state':'pending'}})
        write(raw/'verification.json',{'counts_path':{'source_to_vm':{'state':'pass'}}})
        raw_tar=root/'raw.tar.gz';digest=self.bundle(raw,raw_tar)
        late=root/'late';late.mkdir();write(late/'input-binding.json',{'raw_evidence_export':{'verified':True,'archive_sha256':digest}})
        write_report(late/'gke-evaluation.json',{'analysis':{'state':'complete'},'raw_evidence_export':{'verified':True,'archive_sha256':digest},'spec':{},'resources':{},'selected_accuracy':[],
            'numerical_agreement':{'state':'fail','checks':[{'state':'fail','observed':{'value':1},'matching_references':[{'value':2}]}]},'traffic':{}})
        write(late/'analysis-status.json',{'analysis':{'state':'complete'},'raw_evidence_export':{'verified':True,'archive_sha256':digest}})
        write(late/'worker-status.json',{'state':'complete','returncode':0,'workers_stopped':True})
        for name,value in (extra or {}).items():
            if name=='analysis-status.json':value=dict(value,raw_evidence_export={'verified':True,'archive_sha256':digest})
            write(late/name,value)
        late_tar=root/'late.tar.gz';late_digest=self.bundle(late,late_tar)
        return raw_tar,digest,late_tar,late_digest

    def test_reads_two_archives_without_extracting_or_overwriting_raw(self):
        from evaluation.gke.analysis_reader import read_analysis_report
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);raw,digest,late,late_digest=self.fixtures(root);before=raw.read_bytes()
            with archive_inputs.open_archives(raw,digest,analysis_archive=late,analysis_sha256=late_digest) as bundle:
                seals=bundle.seals
                report,_=read_analysis_report(bundle.extractfile('gke-evaluation.json'),seals['gke-evaluation.json'],open_member=bundle.extractfile)
                self.assertEqual(report['numerical_agreement']['state'],'fail')
                self.assertEqual(json.load(bundle.extractfile('verification.json'))['counts_path']['source_to_vm']['state'],'pass')
                self.assertEqual(bundle.origins['gke-evaluation.json'],'analysis')
                self.assertEqual(bundle.origins['verification.json'],'raw')
            self.assertEqual(raw.read_bytes(),before)

    def test_rejects_raw_member_collision_and_unbound_analysis(self):
        for extra in ({'verification.json':{'replacement':True}},{'input-binding.json':{'raw_evidence_export':{'verified':True,'archive_sha256':'0'*64}}}):
            with tempfile.TemporaryDirectory() as d:
                raw,digest,late,late_digest=self.fixtures(Path(d),extra)
                with self.assertRaises(ValueError):
                    with archive_inputs.open_archives(raw,digest,analysis_archive=late,analysis_sha256=late_digest):pass

    def test_reanalysis_accepts_optional_analysis_archive(self):
        from evaluation.gke.accuracy_reanalysis import reanalyse_archive
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);raw,digest,late,late_digest=self.fixtures(root)
            result=reanalyse_archive(raw,root/'result',expected_sha256=digest,analysis_archive=late,analysis_sha256=late_digest)
            self.assertEqual(result['resources']['original_counts']['fail'],1)
            manifest=json.loads((root/'result/analysis-manifest.json').read_text())
            self.assertEqual(manifest['analysis_archive_sha256'],late_digest)

    def test_missing_checksum_and_unsafe_archive_members_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);raw,digest,late,late_digest=self.fixtures(root)
            with self.assertRaises(ValueError):
                with archive_inputs.open_archives(raw,digest,analysis_archive=late):pass
            bad=root/'unsafe.tar.gz'
            with tarfile.open(bad,'w:gz') as tar:
                for name in ('../escape','GKE-SHA256SUMS'):
                    info=tarfile.TarInfo(name);info.size=0;tar.addfile(info,io.BytesIO())
            with self.assertRaisesRegex(ValueError,'unsafe'):
                with archive_inputs.open_archives(bad,sha(bad)):pass

    def test_reanalysis_rejects_final_report_with_different_raw_binding(self):
        from evaluation.gke.accuracy_reanalysis import reanalyse_archive
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);raw,digest,late,late_digest=self.fixtures(root)
            report=root/'late/gke-evaluation.json';value=json.loads(report.read_text())
            value['raw_evidence_export']={'verified':True,'archive_sha256':'b'*64};write(report,value)
            late_digest=self.bundle(root/'late',late)
            with self.assertRaisesRegex(ValueError,'binding'):
                reanalyse_archive(raw,root/'result',expected_sha256=digest,analysis_archive=late,analysis_sha256=late_digest)
            self.assertFalse((root/'result').exists())

    def test_archive_rejects_failed_authoritative_analysis_with_partial_report(self):
        from evaluation.gke.accuracy_reanalysis import reanalyse_archive
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);raw,digest,late,late_digest=self.fixtures(root,{
                'analysis-status.json':{'analysis':{'state':'failed'}},
                'worker-status.json':{'state':'failed','returncode':1,'workers_stopped':True}})
            with self.assertRaisesRegex(ValueError,'failed'):
                reanalyse_archive(raw,root/'result',expected_sha256=digest,analysis_archive=late,analysis_sha256=late_digest)
            self.assertFalse((root/'result').exists());self.assertTrue((root/'late/gke-evaluation.json').is_file())

    def test_archive_rejects_success_status_with_unstopped_worker(self):
        with tempfile.TemporaryDirectory() as d:
            raw,digest,late,late_digest=self.fixtures(Path(d),{
                'analysis-status.json':{'analysis':{'state':'complete'}},
                'worker-status.json':{'state':'complete','returncode':0,'workers_stopped':False}})
            with self.assertRaisesRegex(ValueError,'worker'):
                with archive_inputs.open_archives(raw,digest,analysis_archive=late,analysis_sha256=late_digest):pass
