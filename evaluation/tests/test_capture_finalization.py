import hashlib
import tempfile
import unittest
from pathlib import Path

from evaluation.capture_finalization import freeze_prefix, SnapshotIncomplete


class MutableReader:
    def __init__(self, frames):
        self.frames = list(frames)
        self.calls = []

    def __call__(self, offset):
        self.calls.append(offset)
        data, inode = self.frames[min(len(self.calls)-1, len(self.frames)-1)]
        return {"inode": inode, "size": len(data), "data": data[offset:offset+8],
                "requested_at_ns": len(self.calls)*100, "received_at_ns": len(self.calls)*100+10}


class FrozenPrefixTests(unittest.TestCase):
    def freeze(self, reader, path, **kwargs):
        return freeze_prefix(reader, path, timeout_seconds=0.05, **kwargs)

    def test_cutoff_finishes_only_intersecting_record_not_new_records(self):
        with tempfile.TemporaryDirectory() as directory:
            reader = MutableReader([(b'{"a":1}', 7), (b'{"a":1}\n{"new":2}\n', 7)])
            path = Path(directory)/'metrics.json'
            result = self.freeze(reader, path)
            self.assertEqual(path.read_bytes(), b'{"a":1}\n')
            self.assertEqual(result['cutoff_bytes'], 7)
            self.assertEqual(result['sealed_bytes'], 8)
            self.assertTrue(result['complete'])
            self.assertGreater(result['completed_at_ns'], result['cutoff_received_at_ns'])
            self.assertEqual(result['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_partial_string_and_utf8_complete_with_real_later_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            full = '{"x":"café"}\n'.encode()
            reader = MutableReader([(full[:10], 7), (full, 7)])
            path = Path(directory)/'traces.json'
            result = self.freeze(reader, path)
            self.assertEqual(path.read_bytes(), full)
            self.assertEqual(result['cutoff_bytes'], 10)
            self.assertTrue(result['complete'])

    def test_complete_cutoff_excludes_continuously_appended_records(self):
        with tempfile.TemporaryDirectory() as directory:
            reader = MutableReader([(b'{}\n{}\n', 7), (b'{}\n{}\n{"new":1}\n', 7)])
            path = Path(directory)/'metrics.json'
            result = self.freeze(reader, path)
            self.assertEqual(path.read_bytes(), b'{}\n{}\n')
            self.assertEqual(result['cutoff_bytes'], result['sealed_bytes'])
            self.assertEqual(reader.calls, [0])

    def test_rotation_preserves_partial_bytes_and_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'metrics.json'
            with self.assertRaisesRegex(SnapshotIncomplete, 'inode'):
                self.freeze(MutableReader([(b'{"a":1}', 7), (b'{"a":1}\n', 8)]), path)
            self.assertEqual(path.read_bytes(), b'{"a":1}')

    def test_truncation_is_not_repaired_by_later_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'metrics.json'
            with self.assertRaisesRegex(SnapshotIncomplete, 'truncat'):
                self.freeze(MutableReader([(b'{"a":12345}', 7), (b'{}\n', 7)]), path)

    def test_unfinished_record_times_out_without_fabricating_newline(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'metrics.json'
            with self.assertRaisesRegex(SnapshotIncomplete, 'deadline') as raised:
                self.freeze(MutableReader([(b'{"a":', 7)]), path)
            self.assertEqual(path.read_bytes(), b'{"a":')
            self.assertFalse(raised.exception.receipt['complete'])

    def test_expanded_byte_limit_is_enforced_and_existing_file_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'metrics.json'
            with self.assertRaisesRegex(SnapshotIncomplete, 'byte bound'):
                self.freeze(MutableReader([(b'{}\n{}\n', 7)]), path, maximum_bytes=4)
            old = path.read_bytes()
            with self.assertRaises(FileExistsError):
                self.freeze(MutableReader([(b'{}\n', 7)]), path)
            self.assertEqual(path.read_bytes(), old)

    def test_invalid_complete_json_is_preserved_but_not_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'metrics.json'
            with self.assertRaisesRegex(SnapshotIncomplete, 'JSON'):
                self.freeze(MutableReader([(b'{bad}\n', 7)]), path)
            self.assertEqual(path.read_bytes(), b'{bad}\n')

    def test_expected_inode_prevents_silent_rebinding(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(SnapshotIncomplete, 'inode'):
                self.freeze(MutableReader([(b'{}\n', 8)]), Path(directory)/'metrics.json', expected_inode=7)

    def test_cutoff_newline_after_chunk_boundary_does_not_consume_next_record(self):
        with tempfile.TemporaryDirectory() as directory:
            frozen = b'{"long":1111}\n'
            reader = MutableReader([(frozen, 7), (frozen+b'{"new":2}\n', 7)])
            path = Path(directory)/'metrics.json'
            result = self.freeze(reader, path)
            self.assertEqual(path.read_bytes(), frozen)
            self.assertEqual(result['sealed_bytes'], len(frozen))

class FrameDecodeTests(unittest.TestCase):
    def test_binary_checksum_and_declared_size_are_required(self):
        import base64
        import gzip
        from types import SimpleNamespace
        from evaluation.capture_finalization import decode_frame
        raw = b'{}\n'
        header = f'7:3:{hashlib.sha256(raw).hexdigest()}\n'.encode()
        response = SimpleNamespace(returncode=0, stdout=header+base64.b64encode(gzip.compress(raw)), stderr=b'')
        inode, size, decoded = decode_frame(response, 0, compressed=True)
        self.assertEqual((inode, size, decoded), (7, 3, raw))
        response.stdout = header+base64.b64encode(gzip.compress(raw+b'bad'))
        with self.assertRaisesRegex(ValueError, 'compressed'):
            decode_frame(response, 0, compressed=True)

    def test_missing_rotation_and_truncation_remain_explicit(self):
        from types import SimpleNamespace
        from evaluation.capture_finalization import decode_frame
        for code, message in [(40, 'rotation'), (41, 'truncation')]:
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, message):
                decode_frame(SimpleNamespace(returncode=code, stdout=b'', stderr=b''), 0)
        with self.assertRaisesRegex(ValueError, 'missing'):
            decode_frame(SimpleNamespace(returncode=0, stdout=b'missing\n', stderr=b''), 0)

class InventoryTests(unittest.TestCase):
    def test_real_shell_inventory_exposes_nested_directory_and_symlink(self):
        import subprocess
        from evaluation.capture_finalization import inventory_script
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'metrics.json').write_text('{}\n')
            (root/'nested').mkdir()
            (root/'nested/bad.json').write_text('{bad')
            (root/'traces.json').symlink_to(root/'metrics.json')
            result=subprocess.run(['sh','-c',inventory_script(str(root))],capture_output=True,text=True,check=True)
            self.assertIn('file\t'+str(root/'metrics.json'),result.stdout)
            self.assertIn('unsupported\t'+str(root/'nested'),result.stdout)
            self.assertIn('unsupported\t'+str(root/'traces.json'),result.stdout)
