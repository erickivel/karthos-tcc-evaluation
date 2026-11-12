import hashlib,importlib.util,json,tempfile,unittest
from pathlib import Path
from evaluation.tests.test_gke_provision import FakeContext
from evaluation.gke import provision
ROOT=Path(__file__).resolve().parents[2]
class NativeIntegrityTests(unittest.TestCase):
 def fixture(self,root):
  experiment={'bundle':{'manifest_hashes':{}}}
  locations={'source':('opt/evaluation','opt/source-manifest.json'),'product':('opt/product','opt/product-manifest.json'),'cache':('opt/evaluation/cache','opt/cache-manifest.json'),'toolchain':('opt/toolchain','opt/toolchain/manifest.json')}
  for kind,(directory,manifest) in locations.items():
   p=root/directory/'fixture';p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(kind.encode());data=json.dumps({'files':{'fixture':hashlib.sha256(p.read_bytes()).hexdigest()}}).encode();m=root/manifest;m.parent.mkdir(parents=True,exist_ok=True);m.write_bytes(data);experiment['bundle']['manifest_hashes'][kind]=hashlib.sha256(data).hexdigest()
  return experiment
 def verifier(self):
  p=ROOT/'infrastructure/controller/image_guard.py';self.assertTrue(p.exists(),'independent native image-integrity guard is required')
  spec=importlib.util.spec_from_file_location('native_image_guard',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m.verify_image
 def test_verifies_all_four_frozen_file_sets(self):
  verify=self.verifier()
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);m=self.fixture(root);self.assertEqual(verify(root,m),{'source':1,'product':1,'cache':1,'toolchain':1})
 def test_empty_source_module_and_empty_manifest_are_rejected(self):
  verify=self.verifier()
  for damaged in ['opt/evaluation/fixture','opt/source-manifest.json']:
   with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);m=self.fixture(root);(root/damaged).write_bytes(b'')
    with self.assertRaises(ValueError):verify(root,m)
 def test_missing_toolchain_file_is_rejected(self):
  verify=self.verifier()
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);m=self.fixture(root);(root/'opt/toolchain/fixture').unlink()
   with self.assertRaises(ValueError):verify(root,m)
class ExecutableStorageTests(unittest.TestCase):
 def helper(self):
  spec=importlib.util.spec_from_file_location('exec_storage_guard',ROOT/'infrastructure/controller/image_guard.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);self.assertTrue(hasattr(m,'verify_executable_directory'),'persistent TMPDIR must be verified executable');return m.verify_executable_directory
 def test_real_probe_runs_and_is_removed(self):
  helper=self.helper()
  with tempfile.TemporaryDirectory() as tmp:
   helper(Path(tmp));self.assertEqual(list(Path(tmp).iterdir()),[])
 def test_noexec_failure_blocks_and_removes_probe(self):
  from unittest.mock import patch
  helper=self.helper()
  with tempfile.TemporaryDirectory() as tmp:
   with patch('subprocess.run',side_effect=PermissionError('noexec')):
    with self.assertRaises(PermissionError):helper(Path(tmp))
   self.assertEqual(list(Path(tmp).iterdir()),[])

class GracefulLaunchTests(unittest.TestCase):
 def context(self,tmp):
  ctx=FakeContext(Path(tmp));ctx.state.update(phase='provisioned',watchdog_armed=True,bootstrap_context={'uri':'gs://evidence/context','generation':'1'},resources=[ctx.instance]);ctx.state['terraform']={'controller':{'controller_name':'controller'},'foundation':{'state_bucket':'state'}};provision.acquire_project_lease(ctx);ctx.state['phase']='provisioned';return ctx
 def test_first_handoff_stops_cleanly_then_starts_without_hard_reset(self):
  with tempfile.TemporaryDirectory() as tmp:
   ctx=self.context(tmp);provision.start_controller(ctx);actions=[c[3] for c in ctx.calls if c[:3]==['gcloud','compute','instances']]
   self.assertNotIn('reset',actions);self.assertLess(actions.index('stop'),actions.index('start'))
 def test_failed_stop_never_starts_or_retries_automatically(self):
  with tempfile.TemporaryDirectory() as tmp:
   ctx=self.context(tmp);command=ctx.command
   def fail(args,**kw):
    if args[:4]==['gcloud','compute','instances','stop']:raise RuntimeError('stop observation interrupted')
    return command(args,**kw)
   ctx.command=fail
   with self.assertRaisesRegex(RuntimeError,'stop observation'):provision.start_controller(ctx)
   self.assertTrue(ctx.state['runtime_requested']);self.assertFalse(any(c[:4]==['gcloud','compute','instances','start'] for c in ctx.calls))
