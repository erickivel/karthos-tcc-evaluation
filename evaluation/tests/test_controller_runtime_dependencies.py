import tempfile
import unittest
from pathlib import Path
from evaluation.gke.bundle import validate_runtime_lock

class RuntimeDependencyTests(unittest.TestCase):
    def test_stale_cached_dependencies_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);tools=root/'toolchain';tools.mkdir()
            expected=root/'protocols/thesis-completion-v2/inputs/requirements.lock'
            expected.parent.mkdir(parents=True);expected.write_text('ijson==3.4.0.post0\n')
            for stale in (None,'PyYAML==6.0.3\n'):
                if stale is not None:(tools/'requirements.lock').write_text(stale)
                with self.assertRaisesRegex(ValueError,'dependency lock'):validate_runtime_lock(root,tools)
            (tools/'requirements.lock').write_bytes(expected.read_bytes())
            validate_runtime_lock(root,tools)
