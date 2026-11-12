"""Run the pinned Helm 4 post-renderer boundary, without Kubernetes access."""
import importlib.util,json,os,shutil,subprocess,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from evaluation.gke.setup import post_renderer
ROOT=Path(__file__).resolve().parents[2]
@unittest.skipUnless(importlib.util.find_spec("yaml"),"requires pinned runtime PyYAML")
class Helm4RendererTests(unittest.TestCase):
 def render(self,mode,*,unreviewed=False):
  helm=shutil.which('helm')
  if not helm:self.skipTest('Helm runtime unavailable')
  version=subprocess.run([helm,'version','--short'],capture_output=True,text=True,check=True).stdout
  if not version.startswith('v4.0.4'):self.skipTest('requires pinned Helm 4.0.4')
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);work=root/'work';work.mkdir();chart=root/'chart';(chart/'templates').mkdir(parents=True)
   (chart/'Chart.yaml').write_text('apiVersion: v2\nname: renderer-fixture\nversion: 0.1.0\n')
   image='registry.example/fixture:1';locked='registry.example/fixture@sha256:'+'a'*64
   (chart/'templates/deployment.yaml').write_text('apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: frontend\nspec:\n  template:\n    metadata: {}\n    spec:\n      containers:\n      - name: frontend\n        image: '+('registry.example/unreviewed:1' if unreviewed else image)+'\n')
   ctx=SimpleNamespace(work=work,result=root/'results',root=ROOT,run_id='renderer-fixture',manifest={'image_lock':{image:locked}},config={'infrastructure':{}})
   plugin=post_renderer(ctx,mode);env=dict(os.environ,HELM_PLUGINS=str(work/'helm-plugins'),PYTHONPATH=str(ROOT))
   p=subprocess.run([helm,'template','fixture',str(chart),'--post-renderer',str(plugin)],cwd=ROOT,env=env,capture_output=True,text=True,timeout=30)
   errors=ctx.result/'infrastructure'/('helm-renderer-'+mode+'.stderr.log')
   return p,locked,errors.read_text() if errors.exists() else ''
 def test_pinned_helm4_executes_demo_and_product_renderer(self):
  import yaml
  for mode,slot in [('demo','app-a'),('karthos','observability')]:
   p,image,_=self.render(mode);self.assertEqual(p.returncode,0,p.stderr)
   deployment=next(x for x in yaml.safe_load_all(p.stdout) if x and x['kind']=='Deployment')
   pod=deployment['spec']['template']['spec'];self.assertEqual(pod['containers'][0]['image'],image);self.assertEqual(pod['nodeSelector']['evaluation.karthos.dev/slot'],slot)
   self.assertEqual(deployment['metadata']['labels']['evaluation.karthos.dev/run-id'],'renderer-fixture')
 def test_renderer_failure_cannot_become_successful_unpinned_deployment(self):
  p,_,errors=self.render('demo',unreviewed=True);self.assertNotEqual(p.returncode,0);self.assertIn('unreviewed image',errors)
