import json,subprocess,sys,tempfile,unittest
from pathlib import Path
from evaluation.gke.auth_timing import install
class AuthTimingTests(unittest.TestCase):
 def test_credential_stdout_passes_through_but_is_never_logged(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'events.ndjson'
   p=subprocess.run([sys.executable,'-m','evaluation.gke.auth_timing','--output',str(path),'--',sys.executable,'-c','print("secret-fixture-token")'],capture_output=True,text=True)
   self.assertEqual(p.returncode,0);self.assertEqual(p.stdout,'secret-fixture-token\n')
   log=path.read_text();self.assertNotIn('secret-fixture-token',log)
   self.assertEqual([json.loads(x)['event'] for x in log.splitlines()],['auth-start','auth-spawned','auth-finished'])
 def test_install_is_idempotent_and_preserves_exec_contract(self):
  import yaml
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'config';p.write_text(yaml.safe_dump({'users':[{'user':{'exec':{'command':'gke-gcloud-auth-plugin','apiVersion':'client.authentication.k8s.io/v1beta1','args':['--use_application_default_credentials']}}}]}))
   self.assertTrue(install(p,Path(tmp)/'events'));self.assertFalse(install(p,Path(tmp)/'events'))
   e=yaml.safe_load(p.read_text())['users'][0]['user']['exec'];self.assertEqual(e['apiVersion'],'client.authentication.k8s.io/v1beta1');self.assertEqual(e['args'][-1],'--use_application_default_credentials')
 def test_timeout_diagnostic_does_not_export_stderr_secrets(self):
  from evaluation.timing_capture import AvailabilityCapture
  e=subprocess.TimeoutExpired(['kubectl'],5,stderr=b'Request Headers: Authorization: Bearer secret-token\nTLS handshake timeout')
  r=AvailabilityCapture._identity_error_detail(e)
  self.assertTrue(r['http_request_logged']);self.assertFalse(r['http_response_logged'])
  self.assertEqual(r['transport_markers'],['TLS handshake timeout'])
  self.assertNotIn('secret-token',json.dumps(r))

 def test_wrapper_runs_outside_repository(self):
  import evaluation.gke.auth_timing as module
  with tempfile.TemporaryDirectory() as tmp:
   log=Path(tmp)/'events'
   p=subprocess.run([sys.executable,str(Path(module.__file__).resolve()),'--output',str(log),'--',sys.executable,'-c','print("credential")'],cwd=tmp,capture_output=True,text=True)
   self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(p.stdout,'credential\n')
 def test_spawn_failure_retains_stage_without_command(self):
  from evaluation.gke.auth_timing import run
  with tempfile.TemporaryDirectory() as tmp:
   log=Path(tmp)/'events'
   self.assertEqual(run(['/nonexistent/secret-command'],log),1)
   self.assertNotIn('secret-command',log.read_text())
   self.assertEqual(json.loads(log.read_text().splitlines()[-1])['event'],'auth-spawn-failed')
