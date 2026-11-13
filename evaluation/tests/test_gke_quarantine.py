import json,tarfile,tempfile,unittest
from pathlib import Path
from evaluation.gke.export import export_projection
from evaluation.gke.models import write

class QuarantineProjectionTests(unittest.TestCase):
 def fixture(self, root):
  source=root/'source';capture=source/'structured/gateway';bad=capture/'raw-attempt-1/traces.json';bad.parent.mkdir(parents=True)
  bad.write_bytes(b'{"password":"credential-fixture","x":"unterminated')
  good=capture/'raw/traces.json';good.parent.mkdir();good.write_text('{}\n')
  write(capture/'archive-attempt-1-status.json',{'attempt':1,'errors':['invalid raw Spans JSON or activity metadata'],'collector_uid':'uid-1','files':[{'path':'traces.json','bytes':bad.stat().st_size,'sha256':__import__('hashlib').sha256(bad.read_bytes()).hexdigest()}],'capture_interval':{'measurement_bounds':[1,2]}})
  write(source/'structured/status.json',{'owner':'0123456789abcdef'})
  write(capture/'pod.json',{'metadata':{'uid':'uid-1','namespace':'karthos-system','annotations':{'evaluation.karthos.dev/capture-owner':'0123456789abcdef'},'labels':{'app.kubernetes.io/component':'gateway'}}})
  with tarfile.open(capture/'raw-files-attempt-1.tar','w') as a:a.add(bad,arcname='./traces.json')
  return source,bad
 def test_invalid_first_valid_later_quarantines_originals_only(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);source,bad=self.fixture(root);seen=[]
   def quarantine(path,metadata):
    seen.append((path.read_bytes(),metadata));return dict(metadata,verified=True,uri='gs://private/run/object',generation='1')
   export_projection(source,root/'out',quarantine=quarantine)
   self.assertEqual(len(seen),2);self.assertTrue(any(b'credential-fixture' in raw for raw,_ in seen))
   self.assertTrue(bad.exists());self.assertTrue((root/'out/structured/gateway/raw/traces.json').exists())
   self.assertFalse((root/'out/structured/gateway/raw-attempt-1/traces.json').exists())
   self.assertFalse((root/'out/structured/gateway/raw-files-attempt-1.tar').exists())
   self.assertNotIn(b'credential-fixture',b''.join(p.read_bytes() for p in (root/'out').rglob('*') if p.is_file()))
 def test_historical_strict_and_unclassified_canonical_stays_fatal(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);source,bad=self.fixture(root)
   with self.assertRaises(ValueError):export_projection(source,root/'strict')
   (source/'structured/gateway/raw/traces.json').write_text('{bad')
   with self.assertRaises(ValueError):export_projection(source,root/'out',quarantine=lambda p,m:dict(m,verified=True,uri='gs://private/x',generation='1'))
 def test_unverified_quarantine_stops_export(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);source,bad=self.fixture(root)
   with self.assertRaises(ValueError):export_projection(source,root/'out',quarantine=lambda p,m:dict(m,verified=False))

 def test_canonical_classified_failure_is_retained_without_scientific_pass(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);source,bad=self.fixture(root)
   canonical=source/'structured/gateway/raw/traces.json';canonical.write_bytes(b'{broken')
   write(canonical.parent.parent/'capture-status.json',{'errors':['invalid raw Spans JSON'],'collector_uid':'uid-1','files':[{'path':'traces.json','bytes':canonical.stat().st_size,'sha256':__import__('hashlib').sha256(canonical.read_bytes()).hexdigest()}]})
   export_projection(source,root/'out',quarantine=lambda p,m:dict(m,verified=True,uri='gs://private/x',generation='1'))
   receipts=json.loads((root/'out/EXPORT-PROJECTION.json').read_text())['quarantine_receipts']
   self.assertEqual(len(receipts),3);self.assertTrue(all(r['scientific_evidence_valid'] is False for r in receipts))
   self.assertFalse((root/'out/structured/gateway/raw/traces.json').exists())
 def test_corrupt_oversized_and_symlink_failed_originals_stay_fatal(self):
  from unittest.mock import patch
  for category in ('corrupt','oversized','symlink','tar-symlink'):
   with self.subTest(category=category),tempfile.TemporaryDirectory() as t:
    root=Path(t);source,bad=self.fixture(root);archive=bad.parent.parent/'raw-files-attempt-1.tar'
    if category=='corrupt':archive.write_bytes(b'not tar')
    if category=='symlink':bad.unlink();bad.symlink_to(source/'structured/gateway/raw/traces.json')
    if category=='tar-symlink':
     with tarfile.open(archive,'w') as a:
      member=tarfile.TarInfo('escape');member.type=tarfile.SYMTYPE;member.linkname='/private/file';a.addfile(member)
    with patch('evaluation.gke.export.MAX_ARCHIVE_BYTES',1 if category=='oversized' else 8*1024**3):
     with self.assertRaises((ValueError,tarfile.TarError)):
      export_projection(source,root/'out',quarantine=lambda p,m:dict(m,verified=True,uri='gs://private/x',generation='1'))

 def test_callback_cannot_put_credential_text_in_receipt(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);source,bad=self.fixture(root)
   with self.assertRaises(ValueError):
    export_projection(source,root/'out',quarantine=lambda p,m:dict(m,verified=True,uri='gs://private/x?access_token=credential-fixture',generation='1'))

 def test_unexpected_loose_snapshot_member_stays_fatal(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);source,bad=self.fixture(root)
   (bad.parent/'unowned.json').write_bytes(b'{bad')
   with self.assertRaisesRegex(ValueError,'unexpected|manifest'):
    export_projection(source,root/'out',quarantine=lambda p,m:dict(m,verified=True,uri='gs://private/x',generation='1'))

 def test_unknown_duplicate_and_changed_archive_members_stay_fatal(self):
  import io
  for names in (['unowned.bin'],['traces.json','./traces.json'],['traces.json']):
   with self.subTest(names=names),tempfile.TemporaryDirectory() as t:
    root=Path(t);source,bad=self.fixture(root)
    with tarfile.open(bad.parent.parent/'raw-files-attempt-1.tar','w') as archive:
     for name in names:
      body=b'changed';member=tarfile.TarInfo(name);member.size=len(body);archive.addfile(member,io.BytesIO(body))
    with self.assertRaisesRegex(ValueError,'unexpected|duplicate|manifest|checksum'):
     export_projection(source,root/'out',quarantine=lambda p,m:dict(m,verified=True,uri='gs://private/x',generation='1'))

class QuarantineUploaderTests(unittest.TestCase):
 def context(self, root, *, denied=False, corrupt=False):
  from types import SimpleNamespace
  from evaluation.gke.models import sha
  path=root/'original';path.write_bytes(b'credential-containing-malformed-original')
  store={};commands=[]
  def command(args,**kwargs):
   commands.append(args)
   if args[2:4]==['objects','describe']:
    uri=args[4]
    if denied:return SimpleNamespace(returncode=1,stderr='403 Permission denied',stdout='')
    return SimpleNamespace(returncode=0 if uri in store else 1,stderr='' if uri in store else '404 NotFound',stdout=json.dumps({'generation':'7'}))
   if '--if-generation-match=0' in args:
    store[args[-1]]=Path(args[-2]).read_bytes()
   else:
    Path(args[-1]).write_bytes(b'corrupt' if corrupt else store[args[-2].split('#')[0]])
   return SimpleNamespace(returncode=0,stderr='',stdout='')
  ctx=SimpleNamespace(run_id='run-1',config={'quarantine':{'bucket':'private-bucket','prefix':'gke-quarantine-v1/runs/run-1/'}},state={'terraform':{'foundation':{'quarantine_bucket':'private-bucket','evidence_bucket':'normal-bucket','controller_service_account':'controller@example.iam.gserviceaccount.com'}}},command=command)
  return ctx,path,{'original_relative_path':'structured/gateway/raw-attempt-1/traces.json','sha256':sha(path),'bytes':path.stat().st_size},commands,store
 def test_generation_zero_and_generation_pinned_checksum_readback(self):
  from evaluation.gke.quarantine import from_context
  with tempfile.TemporaryDirectory() as t:
   ctx,path,metadata,commands,store=self.context(Path(t))
   receipt=from_context(ctx)(path,metadata)
   self.assertTrue(receipt['verified']);self.assertEqual(receipt['generation'],'7')
   self.assertTrue(any('--if-generation-match=0' in c for c in commands))
   self.assertTrue(any('#7' in arg for c in commands for arg in c))
   self.assertEqual(next(iter(store.values())),path.read_bytes());self.assertTrue(path.exists())
 def test_denied_and_failed_readback_leave_original_and_stop(self):
  from evaluation.gke.quarantine import from_context
  for options in ({'denied':True},{'corrupt':True}):
   with self.subTest(options=options),tempfile.TemporaryDirectory() as t:
    ctx,path,metadata,commands,store=self.context(Path(t),**options)
    with self.assertRaises((ValueError,RuntimeError)):from_context(ctx)(path,metadata)
    self.assertTrue(path.exists())
 def test_run_scope_and_separate_bucket_required(self):
  from evaluation.gke.quarantine import from_context,iam_proposal,preflight_requirements
  with tempfile.TemporaryDirectory() as t:
   ctx,path,metadata,commands,store=self.context(Path(t))
   plan=preflight_requirements(ctx)
   self.assertEqual(plan['required_permissions'],['storage.objects.create','storage.objects.get'])
   self.assertEqual(plan['iam_bindings'][0]['condition']['expression'],"resource.name.startsWith('projects/_/buckets/private-bucket/objects/gke-quarantine-v1/runs/run-1/')")
   self.assertFalse(commands)
   ctx.config['quarantine']['prefix']='gke-quarantine-v1/runs/other/'
   with self.assertRaises(ValueError):from_context(ctx)

 def test_access_probe_checks_controller_object_permissions_before_traffic(self):
  from evaluation.gke.quarantine import probe_access
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);ctx,path,metadata,commands,store=self.context(root)
   result=probe_access(ctx,root/'probe')
   self.assertEqual(result['state'],'passed')
   self.assertTrue(result['verified'])
   self.assertEqual(result['scope'],'non-sensitive storage permission probe; no workload traffic')
   self.assertEqual(len(store),1)
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);ctx,*_=self.context(root,denied=True)
   with self.assertRaises(RuntimeError):probe_access(ctx,root/'probe')
