import tempfile
import unittest
from pathlib import Path
from evaluation.runner import tree_digest
from evaluation.pilot import lock_runtime_images


class PinTests(unittest.TestCase):
    def test_same_revision_with_changed_content_cannot_keep_same_pin(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)
            (p/'values.yaml').write_text('version: one\n')
            first=tree_digest(p)
            (p/'values.yaml').write_text('version: two\n')
            self.assertNotEqual(first,tree_digest(p))
            (p/'new.txt').write_text('extra')
            second=tree_digest(p)
            (p/'new.txt').unlink()
            self.assertNotEqual(second,tree_digest(p))

    def test_runtime_image_digest_drift_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)
            lock_runtime_images(p,[{'image':'demo:v1','image_id':'demo@sha256:first'}])
            lock_runtime_images(p,[{'image':'demo:v1','image_id':'demo@sha256:first'}])
            with self.assertRaises(RuntimeError):
                lock_runtime_images(p,[{'image':'demo:v1','image_id':'demo@sha256:second'}])
