import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from evaluation.gke.models import write,REPAIR_V2

LIMIT=5*1024**3
class OptionalDownloadTests(unittest.TestCase):
    def fixture(self,directory,*,size='3',fail=False):
        root=Path(directory);manifest=root/'manifest.json';write(manifest,{'id':'epoch','schedule':REPAIR_V2,'config':{'gcp':{'project_id':'fixture'},'limits':{'optional_local_download_bytes':LIMIT}}})
        calls=[]
        def execute(args,**kwargs):
            calls.append(args)
            self.assertIn('CLOUDSDK_CONFIG',kwargs['env']);self.assertIn('GOOGLE_APPLICATION_CREDENTIALS',kwargs['env'])
            if args[1:4]==['storage','objects','describe']:
                return SimpleNamespace(returncode=0,stdout=json.dumps({'size':size,'generation':'17'}),stderr='')
            self.assertEqual(args[:3],['gcloud','storage','cp'])
            if fail:return SimpleNamespace(returncode=1,stdout='',stderr='bounded transfer failure')
            Path(args[-1]).write_bytes(b'abc');return SimpleNamespace(returncode=0,stdout='',stderr='')
        return root,manifest,calls,execute
    def test_known_generation_download_reserves_and_receipts(self):
        from evaluation.gke.optional_download import download,status
        with tempfile.TemporaryDirectory() as directory:
            root,manifest,calls,execute=self.fixture(directory)
            result=download(root,manifest,'epoch','gs://fixture-karthos-eval-evidence/gke-qualification-v1/runs/epoch/final-summary/run-report.json',root/'review.json',executor=execute)
            self.assertEqual(result['bytes'],3);self.assertEqual(result['generation'],'17');self.assertEqual((root/'review.json').read_bytes(),b'abc')
            self.assertEqual(calls[-1][-2].split('#')[-1],'17')
            self.assertEqual(status(root,'epoch')['reserved_bytes'],3)
            self.assertEqual(status(root,'epoch')['completed'],1)
    def test_unknown_size_rejected_before_download(self):
        from evaluation.gke.optional_download import download
        for size in (None,True,-1,'unknown','1.5'):
            with self.subTest(size=size),tempfile.TemporaryDirectory() as directory:
                root,manifest,calls,execute=self.fixture(directory,size=size)
                with self.assertRaisesRegex(ValueError,'size'):download(root,manifest,'epoch','gs://fixture-karthos-eval-evidence/gke-qualification-v1/runs/epoch/one',root/'review',executor=execute)
                self.assertEqual(len(calls),1)
    def test_cumulative_reservation_rejects_before_transfer(self):
        from evaluation.gke.optional_download import download
        with tempfile.TemporaryDirectory() as directory:
            root,manifest,calls,execute=self.fixture(directory,size=str(LIMIT-1),fail=True)
            uri='gs://fixture-karthos-eval-evidence/gke-qualification-v1/runs/epoch/one'
            with self.assertRaises(RuntimeError):download(root,manifest,'epoch',uri,root/'first',executor=execute)
            _,_,_,small_execute=self.fixture(directory,size='3')
            with self.assertRaisesRegex(ValueError,'5 GiB'):download(root,manifest,'epoch',uri,root/'second',executor=small_execute)
    def test_failed_transfer_consumes_reservation(self):
        from evaluation.gke.optional_download import download,status
        with tempfile.TemporaryDirectory() as directory:
            root,manifest,calls,execute=self.fixture(directory,fail=True)
            with self.assertRaises(RuntimeError):download(root,manifest,'epoch','gs://fixture-karthos-eval-evidence/gke-qualification-v1/runs/epoch/one',root/'review',executor=execute)
            self.assertEqual(status(root,'epoch')['reserved_bytes'],3);self.assertEqual(status(root,'epoch')['failed'],1)
            self.assertFalse((root/'review').exists())
    def test_scope_legacy_wildcards_and_existing_destination_rejected(self):
        from evaluation.gke.optional_download import download
        with tempfile.TemporaryDirectory() as directory:
            root,manifest,calls,execute=self.fixture(directory)
            for uri in ['gs://other/gke-qualification-v1/runs/epoch/one','gs://fixture-karthos-eval-evidence/gke-qualification-v1/runs/other/one','gs://fixture-karthos-eval-evidence/gke-qualification-v1/runs/epoch/*']:
                with self.subTest(uri=uri),self.assertRaises(ValueError):download(root,manifest,'epoch',uri,root/'review',executor=execute)
            (root/'review').write_bytes(b'keep')
            with self.assertRaises(FileExistsError):download(root,manifest,'epoch','gs://fixture-karthos-eval-evidence/gke-qualification-v1/runs/epoch/one',root/'review',executor=execute)
            self.assertEqual(calls,[])
            doc=json.loads(manifest.read_text());doc['schedule']='thesis-repair-validation-v1';write(manifest,doc)
            with self.assertRaises(ValueError):download(root,manifest,'epoch','gs://fixture-karthos-eval-evidence/gke-qualification-v1/runs/epoch/one',root/'another',executor=execute)

    def test_concurrent_downloads_cannot_overreserve(self):
        import concurrent.futures,threading
        from evaluation.gke.optional_download import download,status
        with tempfile.TemporaryDirectory() as directory:
            root,manifest,calls,execute=self.fixture(directory,size=str(LIMIT//2+1),fail=True)
            barrier=threading.Barrier(2)
            def concurrent_execute(args,**kwargs):
                if args[1:4]==['storage','objects','describe']:barrier.wait(timeout=5)
                return execute(args,**kwargs)
            def attempt(number):
                try:download(root,manifest,'epoch','gs://fixture-karthos-eval-evidence/gke-qualification-v1/runs/epoch/one',root/str(number),executor=concurrent_execute)
                except Exception as exc:return type(exc).__name__
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,range(2)))
            self.assertCountEqual(results,['RuntimeError','ValueError'])
            self.assertEqual(status(root,'epoch')['reserved_bytes'],LIMIT//2+1)
            self.assertEqual(sum(args[:3]==['gcloud','storage','cp'] for args in calls),1)

    def test_invalid_generation_rejected_before_transfer(self):
        from evaluation.gke.optional_download import download
        with tempfile.TemporaryDirectory() as directory:
            root,manifest,calls,execute=self.fixture(directory)
            def invalid_generation(args,**kwargs):
                result=execute(args,**kwargs);doc=json.loads(result.stdout);doc['generation']=None;result.stdout=json.dumps(doc);return result
            with self.assertRaisesRegex(ValueError,'generation'):download(root,manifest,'epoch','gs://fixture-karthos-eval-evidence/gke-qualification-v1/runs/epoch/one',root/'review',executor=invalid_generation)
            self.assertEqual(len(calls),1)

    def test_config_cap_required_by_v2_launch_gate(self):
        from evaluation.gke.models import validate_v2_inputs
        with self.assertRaisesRegex(ValueError,'optional local download cap'):
            validate_v2_inputs({'schedule':REPAIR_V2,'config':{}},'epoch')
