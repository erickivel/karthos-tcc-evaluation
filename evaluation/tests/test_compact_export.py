import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from evaluation.gke import export
from evaluation.gke.models import write, read, sha
from evaluation.gke.failure_evidence import retain_attempt_reports
from evaluation.gke.recoverable_run import planning_forecast


class CompactExportTests(unittest.TestCase):
    def test_bundled_export_preserves_members_and_uses_verified_uploader(self):
        self.assertTrue(hasattr(export, 'upload_bundle'))
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);source=root/'source';source.mkdir()
            write(source/'receipt.json',{'status':'passed','password':'fixture-secret'})
            write(source/'details.json',{'count':43})
            captured={}
            def upload(ctx,path,**kw):
                captured.update(path=Path(path),kw=kw)
                self.assertTrue(export.verify(path)['valid'])
                self.assertEqual(len(list(Path(path).glob('*.tar.gz'))),1)
                self.assertTrue(export.verify_archive_privacy(next(Path(path).glob('*.tar.gz'))))
                return {'verified':True,'uri':'gs://evidence/run/readiness/'}
            with patch.object(export,'_upload',side_effect=upload):
                receipt=export.upload_bundle(SimpleNamespace(result=root),source,bucket='evidence',prefix='run/readiness',category='readiness')
            self.assertTrue(receipt['verified'])
            self.assertEqual(receipt['format'],'indexed-bundle-v1')
            self.assertIn('receipt.json',receipt['members'])
            self.assertNotEqual(receipt['members']['receipt.json']['sha256'],sha(source/'receipt.json'))
            self.assertEqual(read(source/'receipt.json')['password'],'fixture-secret')

    def test_metadata_bundle_roundtrip_and_corruption(self):
        self.assertTrue(hasattr(export,'unpack_metadata_bundle'))
        import shutil
        with tempfile.TemporaryDirectory() as t:
            root=Path(t).resolve();source=root/'source';write(source/'checkpoint.json',{'records':[]})
            def publish(ctx,path,**kw):
                shutil.copytree(path,root/'download')
                return {'verified':True,'uri':'gs://evidence/checkpoint/'}
            with patch.object(export,'_upload',side_effect=publish):
                receipt=export.upload_bundle(SimpleNamespace(),source,bucket='evidence',prefix='checkpoint',category='checkpoint')
            export.unpack_metadata_bundle(root/'download',root/'extracted')
            self.assertTrue(export.verify(root/'extracted')['valid'])
            self.assertEqual(sha(root/'extracted/checkpoint.json'),receipt['members']['checkpoint.json']['sha256'])
            (root/'download/window.tar.gz').write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError,'seal'):
                export.unpack_metadata_bundle(root/'download',root/'bad')
            self.assertFalse((root/'bad').exists())

    def test_failed_readback_is_not_a_successful_bundle(self):
        self.assertTrue(hasattr(export,'upload_bundle'))
        with tempfile.TemporaryDirectory() as t:
            source=Path(t)/'source';write(source/'receipt.json',{'ok':True})
            with patch.object(export,'_upload',return_value={'verified':False}):
                with self.assertRaisesRegex(ValueError,'readback'):
                    export.upload_bundle(SimpleNamespace(),source,bucket='evidence',prefix='p',category='readiness')

    def test_checkpoint_references_frozen_exported_reports(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);source=root/'attempt';write(source/'gke-evaluation.json',{'outcome':'insufficient'})
            member={'sha256':sha(source/'gke-evaluation.json'),'source_sha256':sha(source/'gke-evaluation.json'),'bytes':(source/'gke-evaluation.json').stat().st_size}
            receipt={'verified':True,'uri':'gs://evidence/run/window/', 'archive_index':'window-index.json',
                     'objects':{'window-index.json':{'sha256':'a'*64,'bytes':123},'window.tar.gz':{'sha256':'b'*64,'bytes':456}},
                     'retained_members':{'gke-evaluation.json':member}}
            rows=[{'id':'w','attempts':[{'attempt_id':'w-a1','path':str(source),'export':receipt}]}]
            result=retain_attempt_reports(SimpleNamespace(write=write),rows,root/'out')
            files=result['attempts'][0]['files']
            selected=next(x for x in files if x['name']=='gke-evaluation.json')
            self.assertEqual(selected['state'],'export-reference')
            self.assertFalse((root/'out/attempt-reports/w-a1/gke-evaluation.json').exists())
            self.assertEqual(read(root/'out/attempt-reports/w-a1/export-reference.json')['export'],receipt)
            # A changed file cannot borrow the previous content's verification.
            write(source/'gke-evaluation.json',{'outcome':'changed'})
            updated=retain_attempt_reports(SimpleNamespace(write=write),rows,root/'updated')
            self.assertTrue((root/'updated/attempt-reports/w-a1/gke-evaluation.json').exists())

    def test_bundle_upload_records_hashes_and_rejects_corrupt_readback(self):
        from evaluation.gke.metadata_benchmark import LocalTransport
        with tempfile.TemporaryDirectory() as t:
            root=Path(t).resolve();source=root/'source';write(source/'receipt.json',{'population':43})
            transport=LocalTransport(root)
            receipt=export.upload_bundle(SimpleNamespace(command=transport.command),source,bucket='evidence',prefix='p',category='readiness')
            self.assertTrue(receipt['objects'])
            self.assertEqual(len(receipt['objects']),4)
            for item in receipt['objects'].values():self.assertRegex(item['sha256'],r'^[a-f0-9]{64}$')
            original=transport.command
            def corrupt(args,**kw):
                result=original(args,**kw)
                if args[:3]==['gcloud','storage','cp'] and args[3]!='--if-generation-match=0':
                    Path(args[4]).write_bytes(b'corrupt')
                return result
            with self.assertRaisesRegex(ValueError,'readback checksum'):
                export.upload_bundle(SimpleNamespace(command=corrupt),source,bucket='evidence',prefix='new',category='checkpoint')

    def test_object_reference_never_binds_unverified_later_bytes(self):
        from evaluation.gke.metadata_benchmark import LocalTransport
        with tempfile.TemporaryDirectory() as t:
            root=Path(t).resolve();source=root/'source';write(source/'receipt.json',{'count':1})
            transport=LocalTransport(root);original=transport.command
            def change(args,**kw):
                result=original(args,**kw)
                if args[:3]==['gcloud','storage','cp'] and args[3].endswith('/receipt.json'):
                    write(source/'receipt.json',{'count':2})
                return result
            with self.assertRaises(ValueError):
                export._upload(SimpleNamespace(command=change),source,bucket='evidence',prefix='p')

    def test_unbound_receipt_cannot_replace_reports(self):
        from evaluation.gke.failure_evidence import verified_report_reference
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/'report.json';write(path,{'count':1})
            self.assertIsNone(verified_report_reference({'verified':True,'uri':'gs://bucket/run/'},'report.json',path))

    def test_bundle_retains_original_on_malformed_capture(self):
        import gzip
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);source=root/'source';source.mkdir()
            payload=gzip.compress(b'{"name":"metric"}\n')[:-5]
            (source/'broken.ndjson.gz').write_bytes(payload)
            with patch.object(export,'_upload') as upload:
                with self.assertRaises((ValueError,EOFError,OSError)):
                    export.upload_bundle(SimpleNamespace(),source,bucket='evidence',prefix='p',category='checkpoint')
                upload.assert_not_called()
            self.assertEqual((source/'broken.ndjson.gz').read_bytes(),payload)

    def test_split_forecast_counts_checkpoints_and_final_export(self):
        estimates=dict(provision=1200,rebuild=1800,setup=600,capture_setup_restore=870,history=900,retry_reserve=1800,
                       readiness_export=120,window_export=1200,checkpoint_export=60,final_export=1200)
        root=Path(__file__).resolve().parents[2]
        from evaluation.gke.models import build_matrix,COMPLETION_V2
        matrix=build_matrix(read(root/'protocols/thesis-completion-v2/protocol.yaml'),schedule=COMPLETION_V2)
        config={'readiness':{'phase_estimates_seconds':estimates},'limits':{'cleanup_reserve_seconds':1800,'infrastructure_seconds':43200}}
        result=planning_forecast(config,matrix)
        self.assertEqual(result.get('checkpoint_count'),16) #14 windows + history + final
        self.assertEqual(result['seconds'],58740-8*1200+8*120+16*60+1200)
        self.assertFalse(result['fits'])
        estimates['checkpoint_export']=None
        self.assertIn('checkpoint_export',planning_forecast(config,matrix)['missing'])

if __name__=='__main__':unittest.main()
