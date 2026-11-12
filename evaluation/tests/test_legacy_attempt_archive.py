import tempfile,unittest
from pathlib import Path
from evaluation import pilot
from evaluation.protocol import Cell
class LegacyAttemptArchiveTests(unittest.TestCase):
 def test_archive_uses_requested_legacy_phase(self):
  for smoke in (True,False):
   with self.subTest(smoke=smoke),tempfile.TemporaryDirectory() as directory:
    root=Path(directory);cell=Cell('boutique','trace',1);phase='smoke' if smoke else 'measured'
    current=root/phase/cell.slug;current.mkdir(parents=True);(current/'evidence.txt').write_text('retained')
    pilot._archive_attempt(root,cell,smoke=smoke)
    self.assertFalse(current.exists())
    self.assertEqual((root/'failed-attempts'/f'{phase}-{cell.slug}-1'/'evidence.txt').read_text(),'retained')
if __name__=='__main__':unittest.main()
