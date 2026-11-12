import importlib
import json
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
class PreparationTests(unittest.TestCase):
    def module(self,name):
        try:return importlib.import_module('evaluation.gke.'+name)
        except ImportError:self.fail('GKE '+name+' is not implemented')
    def test_matrix_has_46_windows_and_two_clean_clusters(self):
        m=self.module('models');p=json.loads((ROOT/'protocols/gke-qualification-v1/protocol.yaml').read_text());rows=m.build_matrix(p)
        self.assertEqual(len(rows),46);self.assertEqual(sum(x['duration_seconds'] for x in rows),166*60)
        self.assertEqual(sum(x['phase']=='smoke' for x in rows),16)
        self.assertEqual(sum(x['phase']=='baseline' for x in rows),16)
        self.assertEqual(sum(x['phase']=='restart' for x in rows),4)
        for cell in ['boutique-trace','astronomy-trace','boutique-mesh','astronomy-mesh']:
            a=[x['generator'] for x in rows if x['phase']=='baseline' and x['cell']==cell and x['repeat']==1]
            b=[x['generator'] for x in rows if x['phase']=='baseline' and x['cell']==cell and x['repeat']==2]
            self.assertEqual(a,list(reversed(b)))
    def test_no_agent_or_native_scenarios_in_matrix(self):
        m=self.module('models');p=json.loads((ROOT/'protocols/gke-qualification-v1/protocol.yaml').read_text())
        self.assertNotIn('/prompt',json.dumps(m.build_matrix(p)))
        self.assertNotIn('native',json.dumps(m.build_matrix(p)))
    def test_illegal_config_is_rejected(self):
        m=self.module('models');p=json.loads((ROOT/'config/gke.example.yaml').read_text());p['infrastructure']['worker_count']=2
        with self.assertRaises(ValueError):m.validate_config(p)
    def test_implementation_hash_changes_when_runtime_changes(self):
        m=self.module('models')
        with tempfile.TemporaryDirectory() as t:
            r=Path(t);(r/'evaluation').mkdir();(r/'evaluation/a.py').write_text('one')
            a=m.implementation_fingerprint(r);(r/'evaluation/a.py').write_text('two');self.assertNotEqual(a,m.implementation_fingerprint(r))

    def test_stale_foundation_checkpoint_is_pending_without_cloud_writes(self):
        from evaluation.gke.prepare import foundation_checkpoint_check
        from evaluation.gke.provision import foundation_source_hashes
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);module=root/'infrastructure/terraform/foundation';module.mkdir(parents=True)
            path=module/'main.tf';path.write_text('original')
            original=foundation_source_hashes(SimpleNamespace(root=root))
            config={'gcp':{'foundation_reference':{'source_files':original}}}
            self.assertEqual(foundation_checkpoint_check(root,config)['state'],'verified')
            path.write_text('reviewed extra permission')
            check=foundation_checkpoint_check(root,config)
            self.assertEqual(check['state'],'pending')
            self.assertIn('refresh its reference',check['reason'])
            self.assertEqual(config['gcp']['foundation_reference']['source_files'],original)
            self.assertEqual(path.read_text(),'reviewed extra permission')
            self.assertIsNone(foundation_checkpoint_check(root,{'gcp':{}}))
