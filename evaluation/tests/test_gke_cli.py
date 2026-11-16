import tempfile,unittest
from pathlib import Path
from evaluation.gke.cli import parser,dispatch
from evaluation.gke.models import read,write,implementation_hashes,sha
ROOT=Path(__file__).resolve().parents[2]
class CLITests(unittest.TestCase):
 def test_cli_only_exposes_experiment_operations(self):
  p=parser();commands=next(action for action in p._actions if hasattr(action,'choices') and isinstance(action.choices,dict)).choices
  self.assertEqual(set(commands),{'prepare','provision','run','runtime','status','export','teardown','analyse','download','compare-deployments','build','monitor','recover-iap'})
  a=p.parse_args(['provision','--manifest','prepared/manifest.json','--run-id','fixture']);self.assertEqual(a.manifest,'prepared/manifest.json')
 def test_context_uses_manifest_without_an_approval_file(self):
  from evaluation.gke.context import LiveContext
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);config=read(ROOT/'config/gke.example.yaml');manifest=root/'manifest.json'
   write(manifest,{'id':'fixture','config':config,'implementation_hashes':implementation_hashes(root),'artifact_checks':[],'pending_prerequisites':[]})
   calls=[];ctx=LiveContext(root,manifest,executor=lambda *a,**kw:calls.append(a),allow_pending=True)
   self.assertEqual(ctx.run_id,'fixture');self.assertEqual(calls,[]);self.assertFalse((root/'approval.json').exists())
 def test_unresolved_configuration_still_blocks_before_external_commands(self):
  from evaluation.gke.context import LiveContext
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);manifest=root/'manifest.json';write(manifest,{'id':'fixture','config':read(ROOT/'config/gke.example.yaml'),'implementation_hashes':{},'artifact_checks':[]})
   calls=[]
   with self.assertRaisesRegex(ValueError,'readiness'):LiveContext(root,manifest,executor=lambda *a,**kw:calls.append(a))
   self.assertEqual(calls,[])
 def test_source_consistency_remains_reproducibility_not_approval(self):
  from evaluation.gke.context import check_manifest
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'evaluation').mkdir();source=root/'evaluation/a.py';source.write_text('first')
   manifest=root/'manifest.json';write(manifest,{'implementation_hashes':implementation_hashes(root)})
   check_manifest(root,manifest,expected_sha=sha(manifest));source.write_text('changed')
   with self.assertRaisesRegex(ValueError,'source.*changed'):check_manifest(root,manifest)
 def test_analysis_is_new_directory_and_preserves_original(self):
  from evaluation.gke.analysis import analyse
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);source=root/'results/gke-qualification-v1/runs/fixture';source.mkdir(parents=True);write(source/'run-report.json',{'state':'incomplete','windows':[]})
   original=sha(source/'run-report.json');analyse(root,'fixture','analysis-one');self.assertEqual(sha(source/'run-report.json'),original)
   with self.assertRaises(FileExistsError):analyse(root,'fixture','analysis-one')
