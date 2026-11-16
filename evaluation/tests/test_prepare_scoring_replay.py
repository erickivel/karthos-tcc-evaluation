import hashlib,json,tarfile,tempfile,unittest
from pathlib import Path
from evaluation.gke import prepare_scoring_replay as replay
from evaluation.gke.models import write,sha,canonical_sha,implementation_hashes
from evaluation.gke.export import seal
from evaluation.gke.evidence_first import verify_inputs

class PrepareReplayTests(unittest.TestCase):
    def fixture(self,root):
        source=root/'code';(source/'protocols/demo').mkdir(parents=True);write(source/'protocols/demo/metrics.yaml',{'metrics':[]})
        files=implementation_hashes(source)
        manifest={'schedule':'demo','thresholds':{},'product_pins':{'karthos_revision':'fixed'},'protocol_definition':{'name':'demo'},
                  'config':{'evidence_pipeline':'sealed-before-analysis-v1'},'implementation_hashes':files,'implementation_sha256':canonical_sha(files)}
        original=root/'raw';original.mkdir();write(original/'scoring-context.json',{'manifest_sha256':canonical_sha(manifest),'protocol':'demo','evidence_pipeline':'sealed-before-analysis-v1','spec':{'id':'w'},'execution':{'state':'complete'}})
        write(original/'datum.json',{'value':1});seal(original);old_seal=(original/'GKE-SHA256SUMS').read_bytes();old_hash=sha(original/'GKE-SHA256SUMS')
        # Mimic a real redacted projected population, not original bytes.
        write(original/'datum.json',{'value':1,'redacted':'<redacted>'})
        (original/'LOCAL-ORIGINAL-GKE-SHA256SUMS').write_bytes(old_seal)
        write(original/'EXPORT-PROJECTION.json',{'local_original_seal':old_hash,'redacted_files':[{'path':'datum.json'}]});seal(original)
        archive=root/'window.tar.gz'
        with tarfile.open(archive,'w:gz') as tar:
            for member in original.iterdir():tar.add(member,arcname=member.name)
        receipt={'verified':True,'archive':'window.tar.gz','archive_parts':1,'archive_sha256':sha(archive),'original_window_seal_sha256':old_hash}
        receipt_path=root/'receipt.json';manifest_path=root/'manifest.json';write(receipt_path,receipt);write(manifest_path,manifest)
        args={'archive_sha256':sha(archive),'receipt_path':receipt_path,'receipt_sha256':sha(receipt_path),
              'manifest_path':manifest_path,'manifest_sha256':sha(manifest_path),'source_root':source,'verifier_sha256':canonical_sha(files)}
        return archive,args,manifest

    def test_projected_replay_prepares_worker_binding_without_original_equivalence(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);archive,args,manifest=self.fixture(root);before=sha(archive)
            binding_path=replay.prepare(archive,root/'replay',**args);binding=json.loads(binding_path.read_text())
            self.assertEqual(binding['input_population'],'verified-projection')
            window=Path(binding['window']);receipt=binding['raw_evidence_export']
            with self.assertRaisesRegex(ValueError,'seal checksum'):verify_inputs(window,receipt)
            result=verify_inputs(window,receipt,projection_proof=binding['projected_input_proof'])
            self.assertTrue(result['verified']);self.assertEqual(result['input_population'],'verified-projection')
            self.assertEqual(sha(archive),before)
            self.assertEqual(binding['manifest'],manifest)
            write(window/'datum.json',{'tampered':True})
            with self.assertRaises(ValueError):verify_inputs(window,receipt,projection_proof=binding['projected_input_proof'])

    def test_wrong_receipt_manifest_source_and_archive_are_rejected(self):
        for field in ('archive_sha256','receipt_sha256','manifest_sha256','source'):
            with self.subTest(field=field),tempfile.TemporaryDirectory() as d:
                root=Path(d);archive,args,_=self.fixture(root)
                if field=='source':write(args['source_root']/'protocols/demo/metrics.yaml',{'changed':True})
                else:args[field]='0'*64
                with self.assertRaises(ValueError):replay.prepare(archive,root/'replay',**args)
                self.assertFalse((root/'replay').exists())

    def test_manifest_must_match_measurement_producer_hash(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);archive,args,manifest=self.fixture(root)
            manifest['thresholds']={'changed':1};write(args['manifest_path'],manifest);args['manifest_sha256']=sha(args['manifest_path'])
            with self.assertRaisesRegex(ValueError,'manifest'):replay.prepare(archive,root/'replay',**args)
            self.assertFalse((root/'replay').exists())

    def test_versioned_verifier_is_separate_and_frozen(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);archive,args,manifest=self.fixture(root)
            write(args['source_root']/'protocols/demo/new-verifier.json',{'version':2})
            args['verifier_sha256']=canonical_sha(implementation_hashes(args['source_root']))
            binding=json.loads(replay.prepare(archive,root/'replay',**args).read_text())
            self.assertNotEqual(binding['verifier_implementation_sha256'],manifest['implementation_sha256'])
            self.assertEqual(binding['manifest'],manifest)
            write(args['source_root']/'protocols/demo/new-verifier.json',{'version':3})
            with self.assertRaisesRegex(ValueError,'implementation'):
                verify_inputs(binding['window'],binding['raw_evidence_export'],projection_proof=binding['projected_input_proof'])

    def test_local_reseal_cannot_replace_archive_authority(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);archive,args,_=self.fixture(root)
            binding=json.loads(replay.prepare(archive,root/'replay',**args).read_text());window=Path(binding['window'])
            write(window/'datum.json',{'replacement':True});seal(window)
            binding['projected_input_proof']['projected_seal_sha256']=sha(window/'GKE-SHA256SUMS')
            with self.assertRaisesRegex(ValueError,'verified archive seal'):
                verify_inputs(window,binding['raw_evidence_export'],projection_proof=binding['projected_input_proof'])

    def test_archive_path_escape_is_rejected_before_extraction(self):
        import io
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);archive,args,_=self.fixture(root)
            with tarfile.open(archive,'w:gz') as tar:
                info=tarfile.TarInfo('../outside');info.size=1;tar.addfile(info,io.BytesIO(b'x'))
            args['archive_sha256']=sha(archive)
            receipt=json.loads(Path(args['receipt_path']).read_text());receipt['archive_sha256']=args['archive_sha256']
            write(args['receipt_path'],receipt);args['receipt_sha256']=sha(args['receipt_path'])
            with self.assertRaisesRegex(ValueError,'unsafe'):replay.prepare(archive,root/'replay',**args)
            self.assertFalse((root/'replay').exists());self.assertFalse((root/'outside').exists())

    def test_missing_producer_manifest_binding_is_not_invented(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);archive,args,_=self.fixture(root);raw=root/'raw'
            context=json.loads((raw/'scoring-context.json').read_text());context.pop('manifest_sha256');write(raw/'scoring-context.json',context);seal(raw)
            with tarfile.open(archive,'w:gz') as tar:
                for member in raw.iterdir():tar.add(member,arcname=member.name)
            args['archive_sha256']=sha(archive)
            receipt=json.loads(Path(args['receipt_path']).read_text());receipt['archive_sha256']=args['archive_sha256'];write(args['receipt_path'],receipt);args['receipt_sha256']=sha(args['receipt_path'])
            with self.assertRaisesRegex(ValueError,'manifest'):replay.prepare(archive,root/'replay',**args)
            self.assertFalse((root/'replay').exists())

    def test_replay_workspace_is_private_even_with_permissive_umask(self):
        import os,stat
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);archive,args,_=self.fixture(root)
            old=os.umask(0)
            try:replay.prepare(archive,root/'replay',**args)
            finally:os.umask(old)
            for path in [root/'replay',*(root/'replay').rglob('*')]:
                self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o700 if path.is_dir() else 0o600,str(path))
