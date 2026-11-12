import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from evaluation import log_capture

class PollingLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'capture.log'
        self.capture=log_capture.PollingLogCapture('ns','pod','app',self.path,'2026-10-19T00:00:00Z')

    def snapshot(self,text,code=0,err=''):
        with patch('evaluation.log_capture.subprocess.run',return_value=subprocess.CompletedProcess([],code,text,err)) as run:
            self.capture._poll_once()
            return run.call_args

    def test_private_snapshot_uses_the_same_timestamp_and_replay_rules(self):
        record = b'2026-10-19T00:00:01.123456789Z evidence\n'
        with patch('evaluation.log_capture.subprocess.run') as shell:
            seen = []
            def snapshot(namespace, pod, container, cursor, timeout):
                seen.append((namespace, pod, container, cursor, timeout))
                return subprocess.CompletedProcess([], 0, record, b'')
            capture = log_capture.PollingLogCapture(
                'ns', 'pod', 'app', self.path, '2026-10-19T00:00:00Z', snapshot=snapshot)
            capture._poll_once()
            capture._poll_once()
        shell.assert_not_called()
        self.assertEqual(self.path.read_bytes(), record)
        self.assertEqual(seen, [('ns', 'pod', 'app', '2026-10-19T00:00:00Z', 20),
                                ('ns', 'pod', 'app', '2026-10-19T00:00:01Z', 20)])

    def test_overlap_retains_new_identical_boundary_entries(self):
        stamp='2026-10-19T00:00:01.123456789Z '
        self.snapshot(stamp+'same\n'+stamp+'same\n')
        self.snapshot(stamp+'same\n'+stamp+'same\n'+stamp+'same\n'+stamp+'other\n')
        self.assertEqual(self.path.read_text(),stamp+'same\n'+stamp+'same\n'+stamp+'same\n'+stamp+'other\n')
        args=self.snapshot(stamp+'other\n'+stamp+'same\n'+stamp+'same\n'+stamp+'same\n')
        self.assertEqual(len(self.path.read_text().splitlines()),4)
        self.assertIn('--since-time=2026-10-19T00:00:01Z',args.args[0])
        self.assertNotIn('--follow',args.args[0])

    def test_nanoseconds_timezone_and_empty_snapshot(self):
        a='2026-10-19T03:00:01.000000001+03:00 a\n'
        b='2026-10-19T00:00:01.000000002Z b\n'
        self.snapshot(a)
        self.snapshot('')
        self.snapshot(a+b)
        self.assertEqual(self.path.read_text(),a+b)
        self.assertEqual(self.capture.status()['polls'],3)

    def test_equivalent_timezone_boundary_and_subsequent_nanosecond(self):
        self.snapshot('2026-10-19T03:00:01.000000001+03:00 a\n')
        self.snapshot('2026-10-19T00:00:01.000000001Z a\n2026-10-19T00:00:01.000000002Z b\n')
        self.assertEqual(self.path.read_text().splitlines(),['2026-10-19T03:00:01.000000001+03:00 a','2026-10-19T00:00:01.000000002Z b'])
        with self.assertRaisesRegex(ValueError,'out-of-order'):
            self.snapshot('2026-10-19T00:00:00.999999999Z stale\n')

    def test_kubectl_whole_second_replay_and_within_second_reordering(self):
        early='2026-10-19T21:50:36.526429815Z early\n'
        late='2026-10-19T21:50:36.542613758Z late\n'
        extra='2026-10-19T21:50:36.530000000Z newly-emitted\n'
        self.snapshot(early+late)
        self.snapshot(late+early+extra)
        self.snapshot(early+late+extra)
        self.assertEqual(self.path.read_text(),early+late+extra)
        self.assertEqual(self.capture.status()['cursor'],'2026-10-19T21:50:36Z')

    def test_same_payload_distinct_nanos_and_real_duplicates_across_seconds(self):
        a='2026-10-19T00:00:01.000000001Z same\n'
        b='2026-10-19T00:00:01.000000002Z same\n'
        c='2026-10-19T00:00:02.000000001Z same\n'
        self.snapshot(a+b)
        self.snapshot(b+a+a+c+c)
        self.snapshot(c+c+c)
        self.assertEqual(self.path.read_text(),a+b+a+c+c+c)
        self.assertEqual(self.capture.status()['cursor'],'2026-10-19T00:00:02Z')

    def test_initial_cursor_is_explicit_whole_second_utc(self):
        capture=log_capture.PollingLogCapture('ns','p','c',self.path,'2026-10-19T03:00:01.987654321+03:00')
        with patch('evaluation.log_capture.subprocess.run',return_value=subprocess.CompletedProcess([],0,'','')) as run:
            capture._poll_once()
        self.assertIn('--since-time=2026-10-19T00:00:01Z',run.call_args.args[0])

    def test_raw_line_endings_are_preserved(self):
        raw=b'2026-10-19T00:00:01Z payload\r\n'
        self.snapshot(raw)
        self.assertEqual(self.path.read_bytes(),raw)

    def test_timestamp_false_strips_only_timestamp_prefix(self):
        self.capture.timestamps=False
        self.snapshot('2026-10-19T00:00:01.123456789Z EVAL_REQUEST {"x":1}\n2026-10-19T00:00:02Z   spaces\n')
        self.assertEqual(self.path.read_text(),'EVAL_REQUEST {"x":1}\n  spaces\n')

    def test_invalid_and_out_of_order_snapshots_are_reported_without_partial_append(self):
        for value in ('failed to create fsnotify watcher: too many open files\n','2026-10-19T00:00:02Z ok\nnot timestamped\n','2026-10-19T00:00:02Z second\n2026-10-19T00:00:01Z first\n'):
            with self.subTest(value=value):
                capture=log_capture.PollingLogCapture('ns','p','app',Path(self.tmp.name)/'invalid.log','2026-10-19T00:00:00Z')
                with patch('evaluation.log_capture.subprocess.run',return_value=subprocess.CompletedProcess([],0,value,'')):
                    with self.assertRaises(ValueError): capture._poll_once()

    def test_poll_error_and_timeout_invalidate_status(self):
        for failure in (subprocess.CompletedProcess([],1,'','forbidden'),subprocess.TimeoutExpired('kubectl',20)):
            capture=log_capture.PollingLogCapture('ns','p','app',self.path,'2026-10-19T00:00:00Z',poll_interval=30)
            with patch('evaluation.log_capture.subprocess.run',side_effect=failure if isinstance(failure,Exception) else None,return_value=failure):
                capture.start()
                capture.thread.join(timeout=1)
                capture.stop()
            status=capture.status()
            self.assertTrue(status['errors'])
            self.assertEqual(status['returncode'],1)
            self.assertTrue(status['exited_before_stop'])

    def test_stop_final_snapshot_and_idempotent_partial_stop(self):
        self.capture.stop()
        self.capture.stop()
        self.assertEqual(self.capture.status()['polls'],0)
        capture=log_capture.PollingLogCapture('ns','p','app',self.path,'2026-10-19T00:00:00Z',poll_interval=30)
        with patch('evaluation.log_capture.subprocess.run',return_value=subprocess.CompletedProcess([],0,'2026-10-19T00:00:01Z a\n','')) as run:
            capture.start()
            capture.stop()
            capture.stop()
        self.assertGreaterEqual(run.call_count,1)
        self.assertEqual(self.path.read_text(),'2026-10-19T00:00:01Z a\n')
        self.assertEqual(capture.status()['returncode'],0)
        self.assertFalse(capture.status()['exited_before_stop'])


class BoundedLogCaptureTests(unittest.TestCase):
    def test_exact_utf8_bound_retains_bytes_and_refuses_next_append(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'logs.log'
            first='2026-10-19T00:00:01Z π\n'.encode()
            second=b'2026-10-19T00:00:02Z more\n'
            capture=log_capture.PollingLogCapture('ns','pod','app',path,'2026-10-19T00:00:00Z',max_bytes=len(first))
            with patch('evaluation.log_capture.subprocess.run',return_value=subprocess.CompletedProcess([],0,first,b'')):
                capture._poll_once()
            self.assertEqual(path.read_bytes(),first)
            with patch('evaluation.log_capture.subprocess.run',return_value=subprocess.CompletedProcess([],0,first+second,b'')):
                with self.assertRaisesRegex(ValueError,'byte limit'):capture._poll_once()
            self.assertEqual(path.read_bytes(),first)
            self.assertEqual(capture.status()['max_bytes'],len(first))
            self.assertTrue(capture.status()['limit_exceeded'])

    def test_limit_combines_stderr_and_stdout_and_keeps_existing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'logs.log';stderr=path.with_suffix('.stderr')
            first=b'2026-10-19T00:00:01Z first\n'
            path.write_bytes(first);stderr.write_bytes(b'old')
            capture=log_capture.PollingLogCapture('ns','pod','app',path,'2026-10-19T00:00:00Z',max_bytes=len(first)+5)
            with patch('evaluation.log_capture.subprocess.run',return_value=subprocess.CompletedProcess([],1,b'',b'new')):
                with self.assertRaisesRegex(ValueError,'byte limit'):capture._poll_once()
            self.assertEqual(path.read_bytes(),first);self.assertEqual(stderr.read_bytes(),b'old')
            self.assertEqual(capture.status()['retained_bytes'],len(first)+3)

    def test_limit_stops_polling_and_stop_does_not_poll_or_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'logs.log'
            capture=log_capture.PollingLogCapture('ns','pod','app',path,'2026-10-19T00:00:00Z',max_bytes=10)
            with patch('evaluation.log_capture.subprocess.run',return_value=subprocess.CompletedProcess([],0,b'2026-10-19T00:00:01Z overflow\n',b'')) as run:
                capture.start();capture.thread.join(timeout=1);status=capture.stop()
            self.assertEqual(run.call_count,1)
            self.assertEqual(path.read_bytes(),b'')
            self.assertEqual(status['returncode'],1);self.assertTrue(status['errors']);self.assertTrue(status['limit_exceeded'])

    def test_v4_requests_64_mib_while_legacy_has_no_limit(self):
        from evaluation.structured_capture import FastCapture
        from evaluation.capture import ContinuousCapture
        self.assertEqual(FastCapture.log_capture_options,{'max_bytes':64*1024*1024})
        self.assertEqual(ContinuousCapture.log_capture_options,{})

if __name__=='__main__': unittest.main()
