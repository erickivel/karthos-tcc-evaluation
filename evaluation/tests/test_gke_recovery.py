import importlib,tempfile,unittest,json
from pathlib import Path
class RecoveryTests(unittest.TestCase):
 def module(self,n):
  try:return importlib.import_module('evaluation.gke.'+n)
  except ImportError:self.fail(n+' missing')
 def test_export_is_sealed_and_corruption_detected(self):
  e=self.module('export')
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);(p/'ledger.ndjson').write_text('{}\n');e.seal(p);self.assertTrue(e.verify(p)['valid']);(p/'ledger.ndjson').write_text('corrupt');self.assertFalse(e.verify(p)['valid'])
 def test_export_does_not_follow_symlinks_or_credentials(self):
  e=self.module('export')
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);(p/'credentials.json').write_text('SECRET')
   with self.assertRaises(ValueError):e.seal(p)
 def test_missing_manifest_never_executes_mutation(self):
  c=self.module('context');calls=[]
  with tempfile.TemporaryDirectory() as t:
   with self.assertRaises(OSError):c.LiveContext(Path(t),Path(t)/'missing.json',executor=lambda *a,**k:calls.append(a))
  self.assertEqual(calls,[])
 def test_expired_controller_has_no_new_work(self):
  c=self.module('context')
  with self.assertRaises(TimeoutError):c.ensure_budget(100,now=101,reserve=1)

 def test_matrix_preserves_numerical_disagreement(self):
  e=self.module('export')
  with tempfile.TemporaryDirectory() as t:
   rows=e.render_matrix(Path(t),[{'id':'one','phase':'baseline'}],[{'id':'one','state':'diagnostic','evidence_valid':True,'numerical_agreement':False}])
   self.assertEqual(rows[0]['state'],'diagnostic');self.assertFalse(rows[0]['numerical_agreement'])

 def test_matrix_separates_scheduling_mode_from_execution_verdict(self):
  e=self.module('export')
  with tempfile.TemporaryDirectory() as t:
   matrix=[{'id':'one','execution':'serial'},{'id':'two','execution':'serial'}]
   rows=e.render_matrix(Path(t),matrix,[{'id':'one','state':'diagnostic','execution':'complete'}])
   self.assertEqual([row['execution_mode'] for row in rows], ['serial','serial'])
   self.assertEqual([row['execution'] for row in rows], ['complete','unattempted'])
   self.assertEqual(matrix[0]['execution'],'serial')
