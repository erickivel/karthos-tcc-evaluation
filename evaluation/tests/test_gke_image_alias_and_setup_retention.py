import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from evaluation.gke import setup
from evaluation.gke.models import write

class ImageAliasTests(unittest.TestCase):
 def test_actual_injected_observer_alias_has_the_same_approved_digest(self):
  digest='30f1c0d78e0ad60901648be663a710bdadf19e4c10ac6782c235200619158284'
  canonical='docker.io/library/nginx@sha256:'+digest
  rows=[{'container':'eval-observer','image':'nginx:1.28.0-alpine@sha256:'+digest,'image_id':canonical,'status_matches':1}]
  try:setup.validate_running_images(rows,{'historic-local-image-id':canonical})
  except ValueError as error:self.fail('same immutable image rejected due to reference spelling: '+str(error))
 def test_reference_aliases_do_not_authorize_different_repositories_or_digests(self):
  digest='a'*64;lock={'observer':'docker.io/library/nginx@sha256:'+digest}
  for image,runtime in [('evil.example/nginx@sha256:'+digest,'nginx@sha256:'+digest),('nginx:mutable','nginx@sha256:'+digest),('nginx@sha256:'+digest,'nginx@sha256:'+'b'*64)]:
   with self.subTest(image=image,runtime=runtime),self.assertRaises(ValueError):setup.validate_running_images([{'image':image,'image_id':runtime}],lock)
 def test_absent_runtime_or_declared_fields_are_invalid_evidence(self):
  for row in ({},{'image':None,'image_id':None},{'image':'repo/app@sha256:'+'a'*64,'image_id':None}):
   with self.subTest(row=row),self.assertRaises(ValueError):setup.validate_running_images([row],{'app':'repo/app@sha256:'+'a'*64})

class FailedSetupRetentionTests(unittest.TestCase):
 def test_real_driver_records_current_path_before_setup_can_raise(self):
  from evaluation.gke.run import LiveDriver
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);ctx=SimpleNamespace(result=root,write=write);driver=LiveDriver(ctx)
   spec={'id':'smoke-a1','demo':'boutique','source':'mesh','repeat':1,'phase':'smoke','scenario':'matched'}
   def fail(context,cell,parent):
    path=parent/cell.slug;write(path/'demo-running-image-verification.json',{'state':'failed','reason':'fixture'});raise ValueError('setup failed')
   with patch('evaluation.gke.setup.setup_cell',side_effect=fail),self.assertRaisesRegex(ValueError,'setup failed'):driver.setup(spec)
   self.assertIsNotNone(driver.setup_path)
   self.assertEqual(driver.setup_attempt_id,spec['id'])
   from evaluation.gke.failure_evidence import retain_setup_evidence
   target=root/'attempt-diagnostics'/spec['id']/'setup'
   receipt=retain_setup_evidence(ctx,driver,spec,target)
   self.assertEqual(receipt['state'],'retained')
   self.assertEqual(json.loads((target/'demo-running-image-verification.json').read_text())['state'],'failed')
 def test_stale_setup_is_never_attributed_to_another_attempt(self):
  from evaluation.gke.failure_evidence import retain_setup_evidence
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);source=root/'prior';write(source/'proof.json',{'old':True})
   ctx=SimpleNamespace(result=root,write=write);driver=SimpleNamespace(setup_path=source,setup_attempt_id='old')
   target=root/'diagnostics/new/setup';receipt=retain_setup_evidence(ctx,driver,{'id':'new'},target)
   self.assertEqual(receipt['state'],'not-current');self.assertFalse((target/'proof.json').exists())
 def test_source_outside_run_is_rejected(self):
  from evaluation.gke.failure_evidence import retain_setup_evidence
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);run=root/'run';run.mkdir();source=root/'outside';write(source/'proof.json',{})
   ctx=SimpleNamespace(result=run,write=write);driver=SimpleNamespace(setup_path=source,setup_attempt_id='a1')
   with self.assertRaises(ValueError):retain_setup_evidence(ctx,driver,{'id':'a1'},run/'diagnostics/setup')

class SetupArchiveIntegrationTests(unittest.TestCase):
 def execute_failure(self,root,interrupted=False):
  import time,tarfile
  from evaluation.gke import recoverable_run as recovery,export
  testcase=self;uploaded=[]
  spec={'id':'r1-smoke-boutique-trace-k6-matched','repeat':1,'phase':'smoke','cell':'boutique-trace','demo':'boutique','source':'trace','generator':'k6','scenario':'matched','duration_seconds':60,'rate':2}
  ctx=SimpleNamespace(result=root,run_id='unit-check',manifest={'matrix':[spec]},config={'execution_segment':'thesis','limits':{'cleanup_reserve_seconds':10}},deadline=time.time()+3600,state={'resources':[]},write=write,event=lambda *a,**k:None)
  class Driver:
   def __init__(self):self.paths={};self.nodes=[]
   def start_repeat(self,*a):pass
   def setup(self,a):
    self.setup_attempt_id=a['id'];self.setup_path=root/'setups'/a['id'];write(self.setup_path/'demo-running-image-verification.json',{'state':'failed','image':'nginx:fixture@sha256:'+'a'*64})
    if interrupted:raise KeyboardInterrupt('stop failed setup')
    raise ValueError('failed image comparison')
   def window(self,*a):testcase.fail('failed setup must never start traffic')
   def upload(self,a,report):
    path=Path(report['path']);projection=root/'projected'/a['id'];export.export_projection(path,projection);archives=root/'archives'/a['id'];archives.mkdir(parents=True);parts=export.pack_window_archives(projection,archives)
    with tarfile.open(archives/parts[0]['name']) as archive:
     proof=json.load(archive.extractfile('execution-diagnostics/setup/demo-running-image-verification.json'))
     testcase.assertEqual(proof['state'],'failed')
    uploaded.append(a['id']);return {'verified':True,'uri':'gs://fixture/'+a['id']}
   def finish(self):return {'cluster_cleanup':{'residual':[]}}
  class Diagnostics:
   def __init__(self,ctx,path,**kw):self.path=path
   def start(self):self.path.mkdir(parents=True)
   def stop(self):write(self.path/'diagnostics-status.json',{'stopped':True});return {'stopped':True}
  with patch('evaluation.gke.execution_diagnostics.ExecutionDiagnostics',Diagnostics),patch('evaluation.gke.provision.require_context'),patch('evaluation.gke.provision.assert_project_lease'),patch.object(recovery,'transition',return_value={'safe':True}),patch.object(recovery,'_checkpoint',return_value={'verified':True}),patch('evaluation.gke.thesis.claim_matrix',return_value={'claims':[]}):
   report=recovery.execute(ctx,Driver())
  return ctx,report,uploaded
 def test_failed_setup_proof_is_in_each_real_window_archive(self):
  with tempfile.TemporaryDirectory() as d:
   _,report,uploaded=self.execute_failure(Path(d))
   self.assertEqual(len(uploaded),2);self.assertEqual(report['records'][0]['execution'],'unattempted')
 def test_interrupted_setup_proof_survives_the_fatal_diagnostics_archive(self):
  import tarfile
  from evaluation.gke.failure_evidence import copy_summary_diagnostics
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);ctx,report,uploaded=self.execute_failure(root,interrupted=True);self.assertFalse(uploaded)
   copy_summary_diagnostics(ctx,report['records'],root/'summary')
   archive=next((root/'summary').rglob('window.tar.gz'))
   with tarfile.open(archive) as t:self.assertEqual(json.load(t.extractfile('setup/demo-running-image-verification.json'))['state'],'failed')
