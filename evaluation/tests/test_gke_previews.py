import subprocess,tempfile,unittest,json
from pathlib import Path
from evaluation.gke.previews import previews
class PreviewTests(unittest.TestCase):
 def test_client_render_retains_unresolved_images_as_explicit_blockers(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);commands=[]
   def execute(args,**kw):
    commands.append(args);return subprocess.CompletedProcess(args,0,'apiVersion: v1\nkind: Pod\nmetadata:\n  name: example\nspec:\n  containers:\n  - name: app\n    image: unknown:latest\n','')
   result=previews(root,root/'out',root/'baseline',{'infrastructure':{}},{},'fixture',executor=execute)
   self.assertEqual(len(commands),2);self.assertTrue(all(c[:2]==['helm','template'] for c in commands));self.assertEqual(result['state'],'pending');self.assertEqual(len(result['unresolved_images']),2)
   self.assertIn('NEVER APPLY',(root/'out/boutique.yaml').read_text())
