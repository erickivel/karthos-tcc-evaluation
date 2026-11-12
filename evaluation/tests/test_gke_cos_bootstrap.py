"""Exercise COS bootstrap with a Docker stub rejecting read-only home config."""
import os,subprocess,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
class CosBootstrapTests(unittest.TestCase):
 def test_registry_credentials_use_private_writable_path_and_are_removed(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);bindir=root/'bin';bindir.mkdir();work=root/'controller';trace=root/'trace'
   scripts={
    'uname':'#!/bin/sh\necho x86_64\n',
    'curl':'''#!/bin/sh
case "$*" in
 *runtime-enabled) printf false;;
 *controller-image|*bootstrap-sdk-image) printf 'registry.example/image@sha256:%064d' 0;;
 *manifest-uri) printf gs://example/manifest;;
 *manifest-generation) printf 1;;
 *manifest-sha256) printf '%064d' 0;;
 *) exit 42;;
esac
''',
    'sha256sum':'#!/bin/sh\ncat >/dev/null\n',
    'docker':'''#!/bin/sh
case "$1" in
 login)
  [ -n "${DOCKER_CONFIG:-}" ] && [ "$DOCKER_CONFIG" != "$HOME/.docker" ] && [ -d "$DOCKER_CONFIG" ] || exit 73
  cat >/dev/null
  printf fixture-credential > "$DOCKER_CONFIG/config.json"
  printf '%s' "$DOCKER_CONFIG" > "$BOOT_TRACE";;
 logout) rm -f "$DOCKER_CONFIG/config.json";;
 run) case "$*" in *print-access-token*) printf fixture-token;; esac;;
esac
'''}
   for name,body in scripts.items():
    p=bindir/name;p.write_text(body);p.chmod(0o755)
   script=root/'startup.sh';script.write_text((ROOT/'infrastructure/controller/startup.sh').read_text().replace('/var/lib/google/karthos-evaluation',str(work)))
   env=dict(os.environ,PATH=str(bindir)+':'+os.environ['PATH'],HOME=str(root/'readonly-home'),BOOT_TRACE=str(trace));env.pop('DOCKER_CONFIG',None)
   result=subprocess.run(['/bin/bash',str(script)],env=env,capture_output=True,text=True,timeout=10)
   self.assertEqual(result.returncode,0,result.stderr)
   self.assertIn('runtime is dormant',result.stdout)
   config=Path(trace.read_text());self.assertTrue(config.is_relative_to(work));self.assertEqual(config.stat().st_mode&0o777,0o700)
   self.assertFalse((config/'config.json').exists(),'bootstrap credentials must not remain after image pull')
