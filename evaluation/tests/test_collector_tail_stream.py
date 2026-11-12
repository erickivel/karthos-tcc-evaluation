import subprocess
import sys
import unittest

from evaluation.collector_tail_stream import CollectorTailStream


class CollectorTailStreamTests(unittest.TestCase):
    def test_two_ranges_use_the_same_process_and_exact_offsets(self):
        peer = r"""import sys
for line in sys.stdin:
    seq, offset = line.split()
    print(f'begin:{seq}\n3:9:hash\nchunk-at-{offset}\nend:{seq}:0', flush=True)
"""
        with CollectorTailStream([sys.executable, '-u', '-c', peer], 128) as stream:
            a = stream.exchange(3, 2)
            pid = stream.process.pid
            b = stream.exchange(6, 2)
            self.assertEqual(stream.process.pid, pid)
        self.assertEqual(a.stdout, b'3:9:hash\nchunk-at-3')
        self.assertEqual(b.stdout, b'3:9:hash\nchunk-at-6')
        self.assertEqual((a.returncode, b.returncode), (0, 0))

    def test_ready_handshake_validates_peer_response(self):
        peer = r"""import sys
seq, command = sys.stdin.readline().split()
assert command == 'ready'
print(f'begin:{seq}\nready\n\nend:{seq}:0', flush=True)
"""
        with CollectorTailStream([sys.executable, '-u', '-c', peer], 128) as stream:
            stream.handshake(2)

    def test_timeout_retains_partial_frame_and_next_exchange_reconnects(self):
        peer = r"""import sys, time
seq, offset = sys.stdin.readline().split()
print(f'begin:{seq}', flush=True)
if offset == '3':
    print('partial', flush=True)
    time.sleep(30)
else:
    print(f'missing\n\nend:{seq}:0', flush=True)
"""
        with CollectorTailStream([sys.executable, '-u', '-c', peer], 128) as stream:
            with self.assertRaises(subprocess.TimeoutExpired) as caught:
                stream.exchange(3, .5)
            self.assertIn(b'partial', caught.exception.output)
            self.assertIsNone(stream.process)
            self.assertEqual(stream.exchange(4, 2).stdout, b'missing\n')

    def test_wrong_sequence_and_oversized_frames_fail_closed(self):
        for body in (r"print('begin:999\n\nend:999:0', flush=True)", r"print('begin:1\n'+'x'*400, flush=True)"):
            with self.subTest(body=body):
                peer = 'import sys\nsys.stdin.readline()\n'+body
                with CollectorTailStream([sys.executable, '-u', '-c', peer], 128) as stream:
                    with self.assertRaisesRegex(ValueError, 'collector stream'):
                        stream.exchange(0, 2)
                    self.assertIsNone(stream.process)
