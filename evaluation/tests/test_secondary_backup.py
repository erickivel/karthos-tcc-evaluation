import base64,hashlib,json,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

class FakeStore:
    def __init__(self):
        self.calls=[];self.source={'name':'gke-qualification-v1/runs/r/window.tar.gz','generation':'17','size':'3','md5Hash':base64.b64encode(hashlib.md5(b'abc').digest()).decode(),'crc32c':'crc'}
        self.destination=None;self.contents=b'abc';self.fail=False
    def list(self,bucket,prefix):return [self.source] if self.source['name'].startswith(prefix) else []
    def rewrite(self,source,bucket,destination):
        self.calls.append(('rewrite',source['generation'],bucket,destination))
        if self.fail:raise PermissionError('denied')
        if self.destination is None:self.destination={**source,'generation':'23'}
        return self.destination
    def verify(self,bucket,row):
        digest=base64.b64encode(hashlib.md5(self.contents).digest()).decode()
        if digest!=row['md5Hash']:raise ValueError('backup readback mismatch')
        return hashlib.sha256(self.contents).hexdigest()
    def put_receipt(self,bucket,name,data):return {'generation':'31','sha256':hashlib.sha256(data).hexdigest(),'verified':True}

class BackupTests(unittest.TestCase):
    def context(self,root):
        from evaluation.gke.models import write
        return SimpleNamespace(run_id='r',result=Path(root),deadline=time.time()+4000,
            config={'limits':{'cleanup_reserve_seconds':1200}},
            state={'terraform':{'foundation':{'evidence_bucket':'primary','backup_bucket':'secondary'}}},write=write)
    def receipt(self):return {'verified':True,'uri':'gs://primary/gke-qualification-v1/runs/r/final-summary/'}
    def test_backup_verifies_every_object_and_records_generations(self):
        from evaluation.gke.secondary_backup import backup_run
        with tempfile.TemporaryDirectory() as d:
            store=FakeStore();result=backup_run(self.context(d),self.receipt(),store=store)
            self.assertTrue(result['verified'],result)
            self.assertEqual(result['objects'][0]['source_generation'],'17')
            self.assertEqual(result['objects'][0]['backup_generation'],'23')
            self.assertTrue((Path(d)/'secondary-export.json').exists())
    def test_failed_copy_or_corrupt_backup_never_claims_success(self):
        from evaluation.gke.secondary_backup import backup_run
        for corruption in (False,True):
            with self.subTest(corruption=corruption),tempfile.TemporaryDirectory() as d:
                store=FakeStore()
                if corruption:store.contents=b'bad'
                else:store.fail=True
                result=backup_run(self.context(d),self.receipt(),store=store)
                self.assertFalse(result['verified'])
                self.assertTrue(result['errors'])
    def test_existing_matching_backup_can_be_verified_again(self):
        from evaluation.gke.secondary_backup import backup_run
        with tempfile.TemporaryDirectory() as d:
            store=FakeStore();ctx=self.context(d)
            first=backup_run(ctx,self.receipt(),store=store)
            second=backup_run(ctx,self.receipt(),store=store)
            self.assertTrue(first['verified'] and second['verified'])
            self.assertEqual(first['objects'][0]['backup_generation'],second['objects'][0]['backup_generation'])
    def test_missing_budget_does_not_start_copy(self):
        from evaluation.gke.secondary_backup import backup_run
        with tempfile.TemporaryDirectory() as d:
            ctx=self.context(d);ctx.deadline=time.time()+1000;store=FakeStore()
            result=backup_run(ctx,self.receipt(),store=store)
            self.assertFalse(result['verified']);self.assertEqual(store.calls,[])
    def test_unverified_or_wrong_primary_receipt_refused(self):
        from evaluation.gke.secondary_backup import backup_run
        for receipt in ({'verified':False},{'verified':True,'uri':'gs://other/run/'}):
            with tempfile.TemporaryDirectory() as d:
                store=FakeStore();result=backup_run(self.context(d),receipt,store=store)
                self.assertFalse(result['verified']);self.assertEqual(store.calls,[])

    def test_conflicting_existing_backup_is_not_accepted(self):
        from evaluation.gke.secondary_backup import backup_run
        with tempfile.TemporaryDirectory() as d:
            store=FakeStore();store.destination={**store.source,'generation':'23','size':'4'}
            result=backup_run(self.context(d),self.receipt(),store=store)
            self.assertFalse(result['verified'])
            self.assertIn('differs',result['errors'][0]['error'])
    def test_receipt_upload_failure_leaves_preservation_incomplete(self):
        from evaluation.gke.secondary_backup import backup_run
        with tempfile.TemporaryDirectory() as d:
            store=FakeStore()
            with patch.object(store,'put_receipt',side_effect=PermissionError('receipt denied')):
                result=backup_run(self.context(d),self.receipt(),store=store)
            self.assertFalse(result['verified'])
            self.assertTrue(result['objects'][0]['verified'])
            self.assertIn('receipt denied',str(result['errors']))

class RuntimeBackupTests(unittest.TestCase):
    def test_backup_failure_still_requests_ownership_safe_cleanup(self):
        from evaluation.gke import run,secondary_backup,provision
        with patch.object(secondary_backup,'backup_run',side_effect=RuntimeError('backup denied')),patch.object(provision,'request_final_cleanup',return_value={'cleanup_requested':True}) as cleanup:
            backup,result=run.finalize_preservation(SimpleNamespace(),{'verified':True})
        cleanup.assert_called_once()
        self.assertFalse(backup['verified']);self.assertTrue(result['cleanup_requested'])

class GCSContractTests(unittest.TestCase):
    def test_rewrite_pins_source_and_never_overwrites_destination(self):
        from evaluation.gke.secondary_backup import GCS
        client=GCS(SimpleNamespace(),time.time()+120);responses=[{'done':False,'rewriteToken':'next'},{'done':True,'resource':{'generation':'23'}}]
        with patch.object(client,'json',side_effect=responses) as call:
            result=client.rewrite({'name':'a/b','generation':'17'},'primary','secondary')
        self.assertEqual(result['generation'],'23')
        params=call.call_args.args[2]
        self.assertEqual(params,{'sourceGeneration':'17','ifGenerationMatch':0,'rewriteToken':'next'})
        self.assertIn('/o/a%2Fb/rewriteTo/b/secondary/o/a%2Fb',call.call_args.args[1])
    def test_existing_destination_is_verified_not_overwritten(self):
        from urllib.error import HTTPError
        from evaluation.gke.secondary_backup import GCS
        client=GCS(SimpleNamespace(),time.time()+120)
        with patch.object(client,'json',side_effect=[HTTPError('https://fixture',412,'exists',{},None),{'generation':'23'}]) as call:
            self.assertEqual(client.rewrite({'name':'x','generation':'17'},'primary','secondary'),{'generation':'23'})
        self.assertEqual(call.call_args.args[0],'GET')
    def test_readback_is_generation_pinned_and_checks_content(self):
        import io
        from evaluation.gke.secondary_backup import GCS
        client=GCS(SimpleNamespace(),time.time()+120)
        row={'name':'x','generation':'23','size':'3','md5Hash':base64.b64encode(hashlib.md5(b'abc').digest()).decode()}
        with patch.object(client,'request',return_value=io.BytesIO(b'abc')) as request:
            self.assertEqual(client.verify('secondary',row),hashlib.sha256(b'abc').hexdigest())
        self.assertEqual(request.call_args.args[2],{'alt':'media','generation':'23'})
        with patch.object(client,'request',return_value=io.BytesIO(b'bad')):
            with self.assertRaises(ValueError):client.verify('secondary',row)

class GCSWireContentTests(unittest.TestCase):
    def test_json_rewrite_uses_json_content_type_and_empty_metadata_body(self):
        from evaluation.gke.secondary_backup import GCS
        from unittest.mock import MagicMock
        ctx=SimpleNamespace(command=lambda *a,**k:SimpleNamespace(stdout='fixture-token'))
        client=GCS(ctx,time.time()+60)
        with patch('evaluation.gke.secondary_backup.urlopen',return_value=MagicMock()) as opened:
            client.request('POST','https://storage.googleapis.com/storage/v1/b/source/o/x/rewriteTo/b/dest/o/x',{'sourceGeneration':'1','ifGenerationMatch':0})
        req=opened.call_args.args[0]
        self.assertEqual(req.get_header('Content-type'),'application/json')
        self.assertIsNone(req.data)
    def test_media_upload_keeps_original_bytes_and_octet_stream(self):
        from evaluation.gke.secondary_backup import GCS
        from unittest.mock import MagicMock
        client=GCS(SimpleNamespace(command=lambda *a,**k:SimpleNamespace(stdout='fixture-token')),time.time()+60)
        payload=b'opaque bytes'
        with patch('evaluation.gke.secondary_backup.urlopen',return_value=MagicMock()) as opened:
            client.request('POST','https://storage.googleapis.com/upload/storage/v1/b/dest/o',{'uploadType':'media'},payload)
        req=opened.call_args.args[0]
        self.assertEqual(req.get_header('Content-type'),'application/octet-stream')
        self.assertEqual(req.data,payload)
