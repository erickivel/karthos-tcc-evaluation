import unittest
from evaluation.gke import monitor


class HealthTests(unittest.TestCase):
    def test_running_vm_is_not_process_liveness(self):
        result = monitor.controller_health({'status': 'RUNNING'}, None, '')
        self.assertEqual(result['process_state'], 'unknown')
        self.assertEqual(result['provider_state'], 'RUNNING')

    def test_serial_oom_is_retained_evidence_not_current_liveness(self):
        result = monitor.controller_health({'status': 'RUNNING'}, {'Running': True},
            'Out of memory: Killed process 142 (python3) anon-rss:8765432kB\nlogging.logEntries.create denied')
        self.assertEqual(result['process_state'], 'running')
        self.assertTrue(result['serial_python_oom'])
        self.assertTrue(result['logging_permission_denied'])
        self.assertEqual(result['cause'], 'historical OOM evidence; current process running')

    def test_stopped_oom_and_permission_denial_are_separate(self):
        result = monitor.controller_health({'status': 'RUNNING'}, {'Running': False, 'OOMKilled': True, 'ExitCode': 137}, '')
        self.assertEqual(result['process_state'], 'stopped')
        self.assertEqual(result['cause'], 'container OOM kill')
        self.assertFalse(result['logging_permission_denied'])

    def test_monitor_default_interval(self):
        from evaluation.gke.cli import parser
        args = parser().parse_args(['monitor', '--project', 'p', '--zone', 'z', '--controller', 'c', '--owner-id', 'o', '--run-id', 'r', '--uid', '1'])
        self.assertEqual(args.interval, 900)
        self.assertFalse(args.watch)

    def test_provider_failure_never_attempts_ssh_or_reports_dead(self):
        from evaluation.gke.operator_monitor import observe
        from types import SimpleNamespace
        class Unavailable:
            args=SimpleNamespace(run_id='r',interval=900)
            def instance(self):raise RuntimeError('permission denied')
            def ssh(self,*args):raise AssertionError('must not connect')
        result=observe(Unavailable())
        self.assertEqual(result['controller_health']['process_state'],'unknown')
        self.assertTrue(result['keep_observing'])
        self.assertIn('provider',result['observation_errors'])

    def test_project_flag_is_not_forwarded_to_ssh(self):
        import subprocess
        from types import SimpleNamespace
        from evaluation.gke.operator_monitor import Operator
        calls=[]
        def execute(args,**kwargs):
            calls.append(args);return subprocess.CompletedProcess(args,0,'{}','')
        args=SimpleNamespace(project='p',controller='c',zone='z')
        Operator('.',args,executor=execute).ssh('true')
        command=calls[0]
        self.assertLess(command.index('--project=p'),command.index('--'))

class ProgressTests(unittest.TestCase):
    def test_bounded_progress_retains_failed_attempt_after_successful_retry(self):
        import tempfile,json
        from pathlib import Path
        from evaluation.gke.controller_progress import collect
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'r';path.mkdir()
            (path/'matrix.json').write_text(json.dumps([
                {'id':'smoke','state':'complete','attempts':[{'attempt_id':'smoke-a1','state':'failed','execution':'incomplete'},{'attempt_id':'smoke-a2','state':'complete'}]},
                {'id':'baseline','state':'unattempted'}]))
            (path/'run-report.json').write_text(json.dumps({'state':'complete','failure':None}))
            (path/'progress.ndjson').write_text('{"at":123,"phase":"window-exported"}\n')
            result=collect('r',base=Path(d))
            self.assertEqual(result['completed_windows'],1)
            self.assertEqual(result['remaining_windows'],1)
            self.assertEqual(result['failed_attempts'],1)
            self.assertEqual(result['last_phase'],{'at':123,'phase':'window-exported'})
            self.assertEqual(result['terminal_state'],'unknown')
            self.assertTrue(result['keep_observing'])

    def test_oversized_or_missing_progress_is_unknown(self):
        import tempfile
        from pathlib import Path
        from evaluation.gke.controller_progress import collect,JSON_LIMIT
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'r';path.mkdir()
            with (path/'matrix.json').open('wb') as out:out.truncate(JSON_LIMIT+1)
            result=collect('r',base=Path(d))
            self.assertIsNone(result['completed_windows'])
            self.assertEqual(result['terminal_state'],'unknown')
            self.assertIn('matrix',result['errors'])

    def test_run_id_rejects_traversal_and_shell_injection(self):
        from evaluation.gke.controller_progress import collect
        for value in ('../r','r; touch /tmp/no','$(id)'):
            with self.subTest(value=value),self.assertRaises(ValueError):collect(value)

    def test_running_container_does_not_hide_failed_attempts(self):
        import json
        from types import SimpleNamespace
        from evaluation.gke.operator_monitor import observe
        class Running:
            args=SimpleNamespace(run_id='r',interval=900,controller='c',zone='z')
            commands=[]
            def instance(self):return {'status':'RUNNING'}
            def call(self,*args):return ''
            def ssh(self,command):
                self.commands.append(command)
                if 'docker inspect' in command:return json.dumps({'Running':True})
                return json.dumps({'run_id':'r','failed_attempts':2,'completed_windows':1,'remaining_windows':1,'last_phase':{'at':123,'phase':'window-exported'}})
        operator=Running();result=observe(operator)
        self.assertEqual(result['controller_health']['process_state'],'running')
        self.assertEqual(result['progress']['failed_attempts'],2)
        self.assertEqual(result['terminal_state'],'unknown')
        self.assertTrue(result['keep_observing'])
        self.assertIn('docker exec karthos-eval-controller python -m evaluation.gke.controller_progress r',operator.commands[-1])

    def test_progress_transport_failure_does_not_claim_complete(self):
        import json
        from types import SimpleNamespace
        from evaluation.gke.operator_monitor import observe
        class Running:
            args=SimpleNamespace(run_id='r',interval=900,controller='c',zone='z')
            def instance(self):return {'status':'RUNNING'}
            def call(self,*args):return ''
            def ssh(self,command):
                if 'docker inspect' in command:return json.dumps({'Running':True})
                raise RuntimeError('SSH failed')
        result=observe(Running())
        self.assertEqual(result['progress']['state'],'unknown')
        self.assertIn('progress',result['observation_errors'])
        self.assertTrue(result['keep_observing'])

    def test_progress_tail_ignores_truncated_end_and_partial_start(self):
        import tempfile,json
        from pathlib import Path
        from evaluation.gke.controller_progress import collect,TAIL_LIMIT
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'r';path.mkdir()
            (path/'progress.ndjson').write_text('x'*(TAIL_LIMIT+1)+'\n'+json.dumps({'at':4,'phase':'capture'})+'\n{"at":5,')
            result=collect('r',base=Path(d))
            self.assertEqual(result['last_phase'],{'at':4,'phase':'capture'})
            self.assertTrue(result['keep_observing'])

class AccountedWindowTests(unittest.TestCase):
    def test_failed_final_attempt_is_finished_not_waiting_for_more_traffic(self):
        import json,tempfile
        from pathlib import Path
        from evaluation.gke.controller_progress import collect
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'r';p.mkdir()
            (p/'matrix.json').write_text(json.dumps([{'id':'w','state':'failed','retry_policy_complete':True}]))
            result=collect('r',base=Path(d))
            self.assertEqual(result['completed_windows'],0)
            self.assertEqual(result['finished_windows'],1)
            self.assertEqual(result['remaining_windows'],0)
