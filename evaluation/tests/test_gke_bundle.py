import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
try:
    from evaluation.gke.bundle import build_bundle
except ImportError:
    build_bundle=None
from evaluation.gke.models import implementation_hashes, sha
from evaluation.runner import repository_fingerprint, tree_digest

class BundleTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(build_bundle,'offline reviewed bundle builder missing')
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'evaluation';self.root.mkdir()
        for folder in ('evaluation','infrastructure','protocols','config','docs'):
            p=self.root/folder/'sample.txt';p.parent.mkdir(parents=True);p.write_text(folder)
        (self.root/'README.md').write_text('reviewed docs')
        (self.root/'gke-eval.sh').write_text('#!/bin/sh\n');(self.root/'gke-eval.sh').chmod(0o755)
        docker=self.root/'infrastructure/controller/Dockerfile';docker.parent.mkdir();docker.write_text('ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\n')
        self.product=Path(self.temp.name)/'product';self.product.mkdir()
        def git(*args):return subprocess.run(['git',*args],cwd=self.product,check=True,capture_output=True,text=True).stdout.strip()
        git('init','-q');(self.product/'tracked.txt').write_text('tracked')
        git('add','tracked.txt');git('-c','user.name=Offline Test','-c','user.email=offline@example.invalid','commit','-qm','fixture')
        (self.product/'.gitignore').write_text('ignored.txt\n');(self.product/'ignored.txt').write_text('private ignored')
        (self.product/'untracked.txt').write_text('reviewed dirty bytes')
        self.pins={'karthos_source_hash':repository_fingerprint(self.product),'karthos_revision':git('rev-parse','HEAD')}
        for name,key in [('boutique','boutique_source_hash'),('opentelemetry-demo','astronomy_chart_hash'),('karthos-chart','karthos_chart_hash')]:
            p=self.root/'cache'/name;p.mkdir(parents=True);(p/'chart.yaml').write_text(name)
            (p/'.git').mkdir();(p/'.git/private').write_text('must never copy')
            self.pins[key]=tree_digest(p,exclude={'.git'})
    def build(self,review='review'):
        return build_bundle(self.root,review,self.product,self.pins,implementation_hashes(self.root))
    def test_exact_source_product_cache_and_explicit_missing_toolchain(self):
        result=self.build();path=Path(result['path'])
        self.assertEqual(path,self.root/'build/gke/bundles/review')
        source=json.loads((path/'source-manifest.json').read_text())
        self.assertEqual(source['files'],implementation_hashes(self.root))
        product=json.loads((path/'product-manifest.json').read_text())
        self.assertEqual(product['original_source_hash'],self.pins['karthos_source_hash'])
        self.assertIn('untracked.txt',product['files']);self.assertNotIn('ignored.txt',product['files'])
        self.assertFalse((path/'product/.git').exists());self.assertFalse((path/'cache/boutique/.git').exists())
        for name,key in [('boutique','boutique_source_hash'),('opentelemetry-demo','astronomy_chart_hash'),('karthos-chart','karthos_chart_hash')]:
            self.assertEqual(tree_digest(path/'cache'/name),self.pins[key])
        toolchain=json.loads((path/'toolchain/manifest.json').read_text())
        self.assertEqual(toolchain['files'],{});self.assertEqual(toolchain['platform'],'linux/amd64')
        self.assertTrue(any('toolchain' in p for p in result['pending']))
        self.assertEqual(result['manifest_hashes']['source'],sha(path/'source-manifest.json'))
        self.assertIn('SOURCE_MANIFEST_SHA256',(path/'BUILD.md').read_text())
        self.assertTrue((path/'source/gke-eval.sh').stat().st_mode & 0o111)
    def test_mismatched_product_and_stale_source_are_rejected(self):
        (self.product/'tracked.txt').write_text('not reviewed')
        with self.assertRaises(ValueError): self.build()
        self.assertFalse((self.root/'build/gke/bundles/review').exists())
        with self.assertRaises(ValueError): build_bundle(self.root,'stale',None,self.pins,{})
    def test_symlink_escape_credentials_and_private_paths_rejected(self):
        (self.product/'link').symlink_to('/etc/passwd')
        self.pins['karthos_source_hash']=repository_fingerprint(self.product)
        with self.assertRaises(ValueError): self.build()
        (self.product/'link').unlink();(self.product/'credentials.json').write_text('not a real credential')
        self.pins['karthos_source_hash']=repository_fingerprint(self.product)
        with self.assertRaises(ValueError): self.build()
        (self.product/'credentials.json').unlink();(self.product/'.work').mkdir();(self.product/'.work/file').write_text('private')
        self.pins['karthos_source_hash']=repository_fingerprint(self.product)
        with self.assertRaises(ValueError): self.build()
        with self.assertRaises(ValueError): build_bundle(self.root,'../escape',None,self.pins,implementation_hashes(self.root))
    def test_missing_inputs_are_pending_without_fake_product_or_cache(self):
        import shutil
        shutil.rmtree(self.root/'cache/opentelemetry-demo')
        result=build_bundle(self.root,'missing',None,self.pins,implementation_hashes(self.root));path=Path(result['path'])
        self.assertEqual(json.loads((path/'product-manifest.json').read_text())['files'],{})
        self.assertEqual(list((path/'product').iterdir()),[])
        self.assertTrue(any('product' in p for p in result['pending']))
        self.assertTrue(any('opentelemetry-demo' in p for p in result['pending']))
    def test_mismatched_cache_never_packaged_and_review_cannot_be_overwritten(self):
        (self.root/'cache/boutique/chart.yaml').write_text('mutated cache')
        result=self.build();path=Path(result['path'])
        self.assertFalse((path/'cache/boutique').exists())
        self.assertTrue(any('boutique' in p and 'mismatch' in p for p in result['pending']))
        with self.assertRaises(FileExistsError): self.build()
    def test_bundle_maps_are_deterministic_across_review_ids(self):
        a=self.build('first');b=self.build('second')
        self.assertEqual(a['manifest_hashes'],b['manifest_hashes'])
    def test_removed_tracked_paths_are_metadata_never_fabricated_bytes(self):
        (self.product/'tracked.txt').unlink()
        self.pins['karthos_source_hash']=repository_fingerprint(self.product)
        result=self.build();path=Path(result['path'])
        manifest=json.loads((path/'product-manifest.json').read_text())
        self.assertIn('tracked.txt',manifest['missing_tracked_paths'])
        self.assertNotIn('tracked.txt',manifest['files']);self.assertFalse((path/'product/tracked.txt').exists())
    def test_source_symlink_is_rejected_even_if_hash_collector_omits_it(self):
        (self.root/'docs/outside.txt').symlink_to('/etc/passwd')
        with self.assertRaises(ValueError): self.build()
    def test_generated_closures_satisfy_controller_verifier_except_pending_tools(self):
        result=self.build();path=Path(result['path'])
        verifier=Path(__file__).resolve().parents[2]/'infrastructure/controller/verify_bundle.py'
        spec=importlib.util.spec_from_file_location('gke_bundle_verifier',verifier);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        for kind in ('source','product','cache'):
            module.verify_bundle(path/kind,path/(kind+'-manifest.json'),result['manifest_hashes'][kind],kind)
        with self.assertRaises(ValueError):
            module.verify_bundle(path/'toolchain',path/'toolchain/manifest.json',result['manifest_hashes']['toolchain'],'toolchain')

    def test_private_local_config_is_not_baked_into_implementation(self):
        before=implementation_hashes(self.root)
        (self.root/'config/gke.local.yaml').write_text('{"private-account-input":"fixture"}')
        self.assertEqual(implementation_hashes(self.root),before)
        result=self.build()
        self.assertFalse((Path(result['path'])/'source/config/gke.local.yaml').exists())

    def test_known_documentation_alias_is_materialized_without_changing_source_hash(self):
        (self.product/'AGENTS.md').write_text('validated guide')
        (self.product/'CLAUDE.md').symlink_to('AGENTS.md')
        self.pins['karthos_source_hash']=repository_fingerprint(self.product)
        result=self.build();path=Path(result['path'])
        self.assertEqual((path/'product/CLAUDE.md').read_text(),'validated guide')
        self.assertFalse((path/'product/CLAUDE.md').is_symlink())
        self.assertTrue((self.product/'CLAUDE.md').is_symlink())
        manifest=json.loads((path/'product-manifest.json').read_text())
        self.assertEqual(manifest['materialized_documentation_aliases'],{'CLAUDE.md':'AGENTS.md'})

class ToolchainPrivacyTests(unittest.TestCase):
    def test_sdk_executable_and_credential_library_are_code(self):
        from evaluation.gke.bundle import scan_toolchain
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ('google-cloud-sdk/bin/gcloud','google-cloud-sdk/lib/google/auth/credentials.py','google-cloud-sdk/lib/third_party/docker/credentials/__init__.py','google-cloud-sdk/lib/documentation/gcloud/command.py'):
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('# SDK source')
            self.assertEqual(len(scan_toolchain(root)),4)
    def test_actual_private_state_and_keys_are_rejected(self):
        from evaluation.gke.bundle import scan_toolchain
        for name in ('application_default_credentials.json','google-cloud-sdk/credentials.json','google-cloud-sdk/credentials.db','google-cloud-sdk/access_tokens.db','google-cloud-sdk/.config/gcloud/configurations/config_default','bin/id_rsa'):
            with self.subTest(name=name),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('private fixture')
                with self.assertRaises(ValueError):scan_toolchain(root)
    def test_links_and_private_inputs_outside_sdk_are_rejected(self):
        from evaluation.gke.bundle import scan_toolchain
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'gcloud').mkdir();(root/'gcloud/token').write_text('fixture')
            with self.assertRaises(ValueError):scan_toolchain(root)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'link').symlink_to('/etc/passwd')
            with self.assertRaises(ValueError):scan_toolchain(root)

class ArchivedSnapshotTests(BundleTests):
    def test_checksums_preserve_validated_archive_without_git_metadata(self):
        from evaluation.gke.bundle import _product
        original=self.build();archive=Path(original['path'])/'product'
        files,missing,revision,pending=_product(archive,self.pins)
        self.assertEqual(set(files),{'tracked.txt','.gitignore','untracked.txt'})
        self.assertEqual(pending,[])
        self.assertFalse((archive/'.git').exists())
    def test_modified_archive_is_rejected(self):
        from evaluation.gke.bundle import _product
        original=self.build();archive=Path(original['path'])/'product'
        (archive/'tracked.txt').write_text('changed')
        with self.assertRaises(ValueError):_product(archive,self.pins)

    def test_repacked_materialized_alias_preserves_exact_manifest(self):
        (self.product/'AGENTS.md').write_text('validated guide')
        (self.product/'CLAUDE.md').symlink_to('AGENTS.md')
        self.pins['karthos_source_hash']=repository_fingerprint(self.product)
        first=self.build('alias-original');archive=Path(first['path'])
        second=build_bundle(self.root,'alias-repacked',archive/'product',self.pins,implementation_hashes(self.root))
        self.assertEqual((archive/'product-manifest.json').read_bytes(),
                         (Path(second['path'])/'product-manifest.json').read_bytes())
        self.assertEqual(first['manifest_hashes'],second['manifest_hashes'])

    def test_archive_rejects_unsupported_or_unequal_alias_provenance(self):
        from evaluation.gke.bundle import _product
        # Both files belong to the validated closure, but are not aliases.
        (self.product/'AGENTS.md').write_text('guide')
        (self.product/'CLAUDE.md').write_text('different guide')
        self.pins['karthos_source_hash']=repository_fingerprint(self.product)
        original=self.build();archive=Path(original['path'])
        manifest=archive/'product-manifest.json';record=json.loads(manifest.read_text())
        for aliases in ({'CLAUDE.md':'AGENTS.md'},{'tracked.txt':'AGENTS.md'},
                        {'CLAUDE.md':'../AGENTS.md'},[],None):
            with self.subTest(aliases=aliases):
                record['materialized_documentation_aliases']=aliases
                manifest.write_text(json.dumps(record))
                with self.assertRaisesRegex(ValueError,'documentation alias'):
                    _product(archive/'product',self.pins)
