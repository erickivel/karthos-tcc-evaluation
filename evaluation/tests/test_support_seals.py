import tempfile,unittest
from pathlib import Path
from evaluation.gke.models import write,sha
from evaluation.gke.thesis_support import verify_seal

class SupportSealTests(unittest.TestCase):
    def test_modified_or_missing_derived_artifact_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);write(p/'report.json',{'state':'pass'});write(p/'SHA256SUMS',sha(p/'report.json')+'  report.json\n')
            verify_seal(p)
            write(p/'report.json',{'state':'fail'})
            with self.assertRaises(ValueError):verify_seal(p)
            (p/'report.json').unlink()
            with self.assertRaises(ValueError):verify_seal(p)

    def test_escaping_seal_member_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);write(p/'SHA256SUMS','0'*64+'  ../report.json\n')
            with self.assertRaises(ValueError):verify_seal(p)
