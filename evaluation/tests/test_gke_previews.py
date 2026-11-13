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

 def test_legacy_preview_does_not_apply_new_capacity_override(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);commands=[]
   def execute(args,**kw):
    commands.append(args);return subprocess.CompletedProcess(args,0,'','')
   previews(root,root/'out',root/'baseline',{'infrastructure':{},'servicegraph_store_max_items':10000},{},'fixture',executor=execute,product_snapshot=root/'product')
   plans=[c for c in commands if c[0]=='go']
   self.assertEqual(len(plans),4)
   self.assertFalse(any('--servicegraph-store-max-items=10000' in c for c in plans))

 def test_v2_preview_explicitly_applies_active_capacity(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);commands=[]
   def execute(args,**kw):
    commands.append(args);return subprocess.CompletedProcess(args,0,'','')
   previews(root,root/'out',root/'baseline',{'infrastructure':{},'servicegraph_store_max_items':10000},{},'fixture',executor=execute,product_snapshot=root/'product',schedule='thesis-repair-validation-v2')
   self.assertTrue(all('--servicegraph-store-max-items=10000' in c for c in commands if c[0]=='go'))
