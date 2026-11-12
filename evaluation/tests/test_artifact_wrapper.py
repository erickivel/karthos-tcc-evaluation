import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import sys
from evaluation import artifact_wrapper


class ArtifactWrapperTests(unittest.TestCase):
    def setUp(self):
        # macOS has no setsid executable. This fixture invokes the same POSIX
        # setsid syscall before exec; production-image tests use their real tool.
        self.tools=tempfile.TemporaryDirectory()
        self.addCleanup(self.tools.cleanup)
        shim=Path(self.tools.name)/'setsid'
        shim.write_text(f'#!{sys.executable}\nimport os,sys\na=sys.argv[1:]\nif a[0]=="-w":a=a[1:]\nos.setsid()\nos.execvp(a[0],a)\n')
        shim.chmod(0o755)
        from unittest.mock import patch
        self.env_patch=patch.dict(os.environ,PATH=self.tools.name+os.pathsep+os.environ['PATH'])
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def launch(self, directory, command, recovery=2, **extra_env):
        shell=artifact_wrapper.artifact_command('k6',command,'printf "k6 fake version\\n"',recovery_seconds=recovery)
        env=dict(os.environ,RUN_ID='full:run-id',EVAL_ARTIFACT_DIR=str(directory),**extra_env)
        return subprocess.Popen(['/bin/sh','-c',shell],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)

    def ready(self, process, directory):
        deadline=time.monotonic()+3
        while not (directory/'READY').exists() and process.poll() is None and time.monotonic()<deadline:
            time.sleep(.02)
        self.assertTrue((directory/'READY').exists())

    def test_workload_budget_stops_background_descendant_and_publishes_timeout(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp)/'artifacts'
            command='(sleep 3; printf survived > "$EVAL_ARTIFACT_DIR/late-marker") & wait'
            p=self.launch(directory,command,recovery=6,EVAL_WORKLOAD_MAX_SECONDS='1')
            try:
                self.ready(p,directory)
                self.assertEqual((directory/'workload-exit-code.txt').read_text().strip(),'124')
                (directory/'ACK').touch()
                p.communicate(timeout=3)
                self.assertEqual(p.returncode,124)
                time.sleep(3)
                self.assertFalse((directory/'late-marker').exists())
            finally:
                if p.poll() is None:p.kill()
                p.communicate()

    def test_wrapper_interrupt_stops_descendants(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp)/'artifacts'
            p=self.launch(directory,'printf started; (sleep 3; printf survived > "$EVAL_ARTIFACT_DIR/late-marker") & wait',recovery=6,EVAL_WORKLOAD_MAX_SECONDS='30')
            try:
                until=time.monotonic()+2
                while time.monotonic()<until:
                    if (directory/'generator.log').exists() and 'started' in (directory/'generator.log').read_text():break
                    time.sleep(.02)
                self.assertIn('started',(directory/'generator.log').read_text())
                p.terminate()
                p.communicate(timeout=4)
                self.assertEqual(p.returncode,143)
                time.sleep(3)
                self.assertFalse((directory/'late-marker').exists())
            finally:
                if p.poll() is None:p.kill()
                p.communicate()

    def test_deadline_is_rechecked_after_version_command(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp)/'artifacts'
            shell=artifact_wrapper.artifact_command('k6','printf unexpected > "$EVAL_ARTIFACT_DIR/late-marker"','sleep 1; printf version',recovery_seconds=3)
            p=subprocess.Popen(['/bin/sh','-c',shell],env=dict(os.environ,RUN_ID='run',EVAL_ARTIFACT_DIR=str(directory),EVAL_STUDY_DEADLINE_EPOCH=str(int(time.time())+1)),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                self.ready(p,directory)
                self.assertEqual((directory/'workload-exit-code.txt').read_text().strip(),'124')
                self.assertFalse((directory/'late-marker').exists())
                (directory/'ACK').touch()
                p.communicate(timeout=3)
                self.assertEqual(p.returncode,124)
            finally:
                if p.poll() is None:p.kill()
                p.communicate()

    def test_expired_study_deadline_skips_workload_but_retains_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp)/'artifacts'
            p=self.launch(directory,'printf unexpected > "$EVAL_ARTIFACT_DIR/late-marker"',recovery=3,EVAL_STUDY_DEADLINE_EPOCH=str(int(time.time())-1))
            try:
                self.ready(p,directory)
                self.assertEqual((directory/'workload-exit-code.txt').read_text().strip(),'124')
                self.assertFalse((directory/'late-marker').exists())
                self.assertIsNone(p.poll())
                (directory/'ACK').touch()
                p.communicate(timeout=3)
                self.assertEqual(p.returncode,124)
            finally:
                if p.poll() is None:p.kill()
                p.communicate()

    def test_exit_99_publishes_valid_stable_hashes_and_waits_for_ack(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp)/'artifact files'
            p=self.launch(directory,'printf "hello\\n"; printf "{}\\n" > "$EVAL_ARTIFACT_DIR/k6-summary.json"; printf "granular\\n" > "$EVAL_ARTIFACT_DIR/k6.ndjson"; exit 99')
            try:
                self.ready(p,directory)
                self.assertIsNone(p.poll())
                self.assertEqual((directory/'workload-exit-code.txt').read_text().strip(),'99')
                self.assertEqual((directory/'run-id.txt').read_text().strip(),'full:run-id')
                sums=(directory/'SHA256SUMS').read_bytes()
                manifest={}
                for line in sums.decode().splitlines():
                    digest,name=line.split('  ',1)
                    manifest[name]=digest
                    self.assertEqual(hashlib.sha256((directory/name).read_bytes()).hexdigest(),digest)
                self.assertEqual(set(manifest),{'generator.log','generator-version.txt','run-id.txt','workload-started-at.txt','workload-ended-at.txt','workload-exit-code.txt','k6.ndjson','k6-summary.json'})
                self.assertEqual((directory/'generator.log').read_text(),'hello\n')
                (directory/'ACK').touch()
                out,err=p.communicate(timeout=3)
                self.assertEqual(p.returncode,99,err)
                self.assertNotIn('hello',out)
                self.assertEqual((directory/'SHA256SUMS').read_bytes(),sums)
            finally:
                if p.poll() is None:p.kill()
                p.communicate()

    def test_recovery_timeout_keeps_workload_exit_code(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp)/'artifacts'
            p=self.launch(directory,'exit 0',2)
            out,err=p.communicate(timeout=5)
            self.assertEqual(p.returncode,124,err)
            self.assertEqual((directory/'workload-exit-code.txt').read_text().strip(),'0')
            self.assertTrue((directory/'READY').exists())

    def test_locust_reports_use_canonical_filenames_after_process_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp)/'artifacts'
            command=('for part in stats failures exceptions stats_history; do '
                     'printf "report\\n" > "$EVAL_ARTIFACT_DIR/locust_${part}.csv"; done; '
                     'printf "<html>report</html>" > "$EVAL_ARTIFACT_DIR/locust-report.html"; exit 1')
            shell=artifact_wrapper.artifact_command('locust',command,'printf locust-version',recovery_seconds=3)
            p=subprocess.Popen(['/bin/sh','-c',shell],env=dict(os.environ,RUN_ID='run',EVAL_ARTIFACT_DIR=str(directory)),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                self.ready(p,directory)
                for part in ('stats','failures','exceptions','stats_history'):
                    self.assertTrue((directory/f'locust-{part}.csv').is_file())
                    self.assertFalse((directory/f'locust_{part}.csv').exists())
                (directory/'ACK').touch()
                p.communicate(timeout=3)
                self.assertEqual(p.returncode,1)
            finally:
                if p.poll() is None:p.kill()
                p.communicate()

    def test_version_failure_is_initialization_error_without_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp)/'artifacts'
            shell=artifact_wrapper.artifact_command('k6','printf unexpected-workload',
                    'printf "version unavailable reason\\n" >&2; exit 3',recovery_seconds=1)
            result=subprocess.run(['/bin/sh','-c',shell],env=dict(os.environ,RUN_ID='run',EVAL_ARTIFACT_DIR=str(directory)),capture_output=True,text=True,timeout=3)
            self.assertEqual(result.returncode,70)
            self.assertFalse((directory/'READY').exists())
            self.assertIn('version unavailable reason',result.stderr)
            self.assertIn('3',result.stderr)
            self.assertNotIn('unexpected-workload',(directory/'generator.log').read_text())
            self.assertIn('version unavailable reason',(directory/'generator-version.txt').read_text())

    def test_checksum_failure_does_not_publish_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp)/'artifacts'
            bin_dir=Path(temp)/'bin'
            bin_dir.mkdir()
            tool=bin_dir/'sha256sum'
            tool.write_text('#!/bin/sh\nexit 9\n')
            tool.chmod(0o755)
            shell=artifact_wrapper.artifact_command('k6','exit 99','printf version',recovery_seconds=2)
            result=subprocess.run(['/bin/sh','-c',shell],env=dict(os.environ,PATH=str(bin_dir)+os.pathsep+os.environ['PATH'],RUN_ID='run',EVAL_ARTIFACT_DIR=str(directory)),capture_output=True,text=True,timeout=3)
            self.assertNotEqual(result.returncode,0)
            self.assertFalse((directory/'READY').exists())
            self.assertEqual((directory/'workload-exit-code.txt').read_text().strip(),'99')

    def test_initialization_failure_never_publishes_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp)/'not-a-directory'
            directory.write_text('file')
            p=self.launch(directory,'exit 0')
            p.communicate(timeout=3)
            self.assertNotEqual(p.returncode,0)
            self.assertFalse((directory/'READY').exists())

if __name__=='__main__':unittest.main()
