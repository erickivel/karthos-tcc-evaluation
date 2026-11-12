"""Regression for compact receipts spending minutes on serial CLI startup."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from evaluation.gke import export


class ObjectStoreContext:
    def __init__(self, corrupt=False):
        self.objects = {}
        self.calls = []
        self.active = self.peak = 0
        self.guard = threading.Lock()
        self.corrupt = corrupt

    def command(self, args, **kwargs):
        with self.guard:
            self.active += 1
            self.peak = max(self.active, self.peak)
            self.calls.append((list(args), kwargs))
        try:
            time.sleep(.015)
            if args[:4] == ['gcloud', 'storage', 'objects', 'describe']:
                uri = args[4]
                return SimpleNamespace(returncode=0 if uri in self.objects else 1,
                                       stdout='{}', stderr='' if uri in self.objects else '404 not found')
            self.assert_copy(args)
            parts = [x for x in args[3:] if not x.startswith('--')]
            source, destination = parts
            if source.startswith('gs://'):
                Path(destination).write_bytes(b'corrupt' if self.corrupt else self.objects[source])
            else:
                if '--if-generation-match=0' not in args:
                    raise AssertionError('immutable creation precondition missing')
                with self.guard:
                    if destination in self.objects:
                        raise AssertionError('attempted overwrite')
                    self.objects[destination] = Path(source).read_bytes()
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        finally:
            with self.guard:
                self.active -= 1

    @staticmethod
    def assert_copy(args):
        if args[:3] != ['gcloud', 'storage', 'cp']:
            raise AssertionError('unexpected command')


class PublicationConcurrencyTests(unittest.TestCase):
    def fixture(self, root):
        for n in range(17):
            p = root / ('api-polls/poll-%02d.json' % n)
            p.parent.mkdir(exist_ok=True)
            p.write_text(json.dumps({'value': n, 'source': 'mesh.istio', 'rpc.grpc.status_code': '13'}))

    def test_compact_export_is_concurrent_bounded_and_retains_every_member(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            ctx = ObjectStoreContext()
            receipt = export._upload(ctx, root, bucket='fixture-evidence', prefix='run/case')
            self.assertGreater(ctx.peak, 1, 'serial process startup reproduces the live bottleneck')
            self.assertLessEqual(ctx.peak, 4)
            self.assertTrue(receipt['verified'])
            self.assertEqual(receipt['files'], 18)
            self.assertEqual(len(ctx.objects), 18)
            for p in export.evidence_files(root):
                self.assertEqual(ctx.objects['gs://fixture-evidence/run/case/'+str(p.relative_to(root.resolve()))], p.read_bytes())
            self.assertEqual(ctx.active, 0)

    def test_one_corrupt_readback_prevents_ack_and_leaves_no_transfer_running(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            ctx = ObjectStoreContext(corrupt=True)
            with self.assertRaisesRegex(ValueError, 'readback checksum mismatch'):
                export._upload(ctx, root, bucket='fixture-evidence', prefix='run/case')
            self.assertEqual(ctx.active, 0)

    def test_existing_different_object_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            ctx = ObjectStoreContext()
            uri = 'gs://fixture-evidence/run/case/api-polls/poll-00.json'
            ctx.objects[uri] = b'previous immutable evidence'
            with self.assertRaisesRegex(ValueError, 'readback checksum mismatch'):
                export._upload(ctx, root, bucket='fixture-evidence', prefix='run/case')
            self.assertEqual(ctx.objects[uri], b'previous immutable evidence')
            self.assertEqual(ctx.active, 0)
