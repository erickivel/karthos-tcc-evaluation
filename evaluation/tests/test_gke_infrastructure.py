from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[2]/'infrastructure'
class InfrastructureTests(unittest.TestCase):
    def test_roots_exist_and_provider_is_pinned(self):
        for root in ['foundation','controller','cluster']:
            text=(ROOT/'terraform'/root/'versions.tf').read_text()
            self.assertIn('= 7.29.0',text)
            self.assertIn('backend "gcs"',text)
    def test_evidence_has_retention_no_delete_lifecycle(self):
        text=(ROOT/'terraform/foundation/main.tf').read_text()
        self.assertIn('7776000',text)
        self.assertIn('prevent_destroy = true',text)
        self.assertNotIn('lifecycle_rule',text)
        self.assertIn('public_access_prevention',text)
    def test_cluster_roles_and_fixed_pool(self):
        text=(ROOT/'terraform/cluster/main.tf').read_text()
        for term in ['observability','app-a','app-b','e2-standard-4','UBUNTU_CONTAINERD','node_count','evaluation.karthos.dev/role','evaluation.karthos.dev/slot']:
            self.assertIn(term,text)
        self.assertNotIn('autoscaling {',text)
    def test_controller_startup_keeps_integrity_and_explicit_runtime_gate(self):
        text=(ROOT/'controller/startup.sh').read_text()
        for term in ['@sha256:','manifest-sha256','runtime-enabled','runtime --manifest']:
            self.assertIn(term,text)
        self.assertNotIn('approval-receipt',text)
        self.assertLess(text.index('metadata runtime-enabled'),text.index('exec docker run'))

class PackagingTests(unittest.TestCase):
    def test_bundle_verifier_rejects_missing_manifest(self):
        import importlib.util, tempfile
        path=ROOT/'controller/verify_bundle.py'
        self.assertTrue(path.exists(), 'reviewed bundle verifier missing')
        spec=importlib.util.spec_from_file_location('bundle',path)
        mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):mod.verify_bundle(Path(tmp),Path(tmp)/'manifest.json','0'*64,'toolchain')
    def test_docker_packages_reviewed_inputs_and_linux_toolchain(self):
        text=(ROOT/'controller/Dockerfile').read_text()
        for item in ['linux/amd64','SOURCE_MANIFEST_SHA256','TOOLCHAIN_MANIFEST_SHA256','PRODUCT_MANIFEST_SHA256','COPY source/','COPY cache/','COPY cache-manifest.json','/opt/product','--require-hashes','--no-index']:
            self.assertIn(item,text)
    def test_runtime_keeps_persistent_data_and_retained_container(self):
        text=(ROOT/'controller/startup.sh').read_text()
        for item in ['GKE_EVAL_DATA_ROOT=/data','--read-only','--cidfile','--label','/data:/data','/experiment:ro','/opt/product','--platform linux/amd64']:
            self.assertIn(item,text)
        runtime=text[text.index('# Runtime performs'):]
        self.assertNotIn('--rm',runtime)
    def test_toolchain_rejects_host_macho_and_missing_commands(self):
        import importlib.util, tempfile, json, hashlib
        path=ROOT/'controller/verify_bundle.py'
        spec=importlib.util.spec_from_file_location('bundle',path)
        mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'bin').mkdir();binary=root/'bin/kubectl';binary.write_bytes(b'host MachO');binary.chmod(0o755)
            manifest=root/'manifest.json';manifest.write_text(json.dumps({'platform':'linux/amd64','files':{'bin/kubectl':hashlib.sha256(binary.read_bytes()).hexdigest()},'versions':{'kubectl':'1.34.0'}}))
            with self.assertRaisesRegex(ValueError,'Linux ELF amd64'):
                mod.verify_bundle(root,manifest,hashlib.sha256(manifest.read_bytes()).hexdigest(),'toolchain')
    def test_source_requires_complete_reviewed_implementation(self):
        import importlib.util, tempfile, json, hashlib
        path=ROOT/'controller/verify_bundle.py';spec=importlib.util.spec_from_file_location('bundle',path)
        mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'README.md').write_text('incomplete')
            manifest=root/'manifest.json';manifest.write_text(json.dumps({'files':{'README.md':hashlib.sha256(b'incomplete').hexdigest()}}))
            with self.assertRaisesRegex(ValueError,'required reviewed source'):
                mod.verify_bundle(root,manifest,hashlib.sha256(manifest.read_bytes()).hexdigest(),'source')
    def test_missing_runtime_tools_refused_even_with_valid_elf(self):
        import importlib.util, tempfile, json, hashlib
        path=ROOT/'controller/verify_bundle.py';spec=importlib.util.spec_from_file_location('bundle',path)
        mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'bin').mkdir();binary=root/'bin/kubectl'
            binary.write_bytes(b'\x7fELF\x02\x01'+b'\0'*12+b'\x3e\x00');binary.chmod(0o755)
            manifest=root/'manifest.json';manifest.write_text(json.dumps({'platform':'linux/amd64','files':{'bin/kubectl':hashlib.sha256(binary.read_bytes()).hexdigest()},'versions':{'kubectl':'1.34.0'}}))
            with self.assertRaisesRegex(ValueError,'missing pinned runtime tool: helm'):
                mod.verify_bundle(root,manifest,hashlib.sha256(manifest.read_bytes()).hexdigest(),'toolchain')
    def test_startup_missing_manifest_never_launches_container(self):
        import tempfile, subprocess, os
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);binpath=root/'bin';binpath.mkdir();trace=root/'docker-trace'
            scripts={'curl':'#!/bin/sh\nprintf "registry.example/controller@sha256:%064d" 0\n',
                     'uname':'#!/bin/sh\necho x86_64\n',
                     'gcloud':'#!/bin/sh\n# Missing generation-pinned manifest object.\nexit 23\n',
                     'docker':f'#!/bin/sh\necho "$*" >> "{trace}"\ncase "$*" in *"storage cp"*) exit 23;; esac\n',
                     'sha256sum':'#!/bin/sh\nexit 0\n'}
            for name,content in scripts.items():
                path=binpath/name;path.write_text(content);path.chmod(0o755)
            startup=root/'startup.sh';startup.write_text((ROOT/'controller/startup.sh').read_text().replace('/var/lib/google/karthos-evaluation',str(root/'controller')))
            result=subprocess.run(['/bin/bash',str(startup)],env=dict(os.environ,PATH=str(binpath)+':'+os.environ['PATH']),capture_output=True)
            self.assertEqual(result.returncode,23)
            self.assertTrue(trace.exists(),'COS bootstrap must use the pinned SDK container')
            calls=trace.read_text()
            self.assertIn('storage cp',calls)
            self.assertIn('--network host',calls)
            self.assertNotIn('--name karthos-eval-controller',calls,'missing manifest must prevent controller launch')

class ResourceNamingTests(unittest.TestCase):
    def test_actual_terraform_names_are_bounded_and_preserve_identity(self):
        import shutil, tempfile, subprocess, json
        terraform=shutil.which('terraform')
        if not terraform:self.skipTest('Terraform unavailable; name expression evaluation not performed')
        def evaluate(root,run_id):
            names=ROOT/'terraform'/root/'names.tf'
            self.assertTrue(names.exists(),'bounded Terraform naming expressions missing')
            with tempfile.TemporaryDirectory() as tmp:
                work=Path(tmp);(work/'main.tf').write_text(names.read_text()+'\nvariable "owner_id" { type = string }\nvariable "run_id" { type = string }\n')
                expression='jsonencode(local.cluster_name)' if root=='cluster' else 'jsonencode(local.resource_prefix)'
                result=subprocess.run([terraform,'-chdir='+tmp,'console','-var=owner_id='+'a'*48,'-var=run_id='+run_id],input=expression+'\n',text=True,capture_output=True)
                self.assertEqual(result.returncode,0,result.stderr)
                return json.loads(json.loads(result.stdout.strip()))
        first=evaluate('cluster','a'*48);second=evaluate('cluster','b'*48)
        self.assertLessEqual(len(first),40)
        self.assertNotEqual(first,second)
        self.assertEqual(first,evaluate('cluster','a'*48))
        prefix=evaluate('controller','a'*48)
        self.assertEqual(prefix,evaluate('controller','b'*48),'controller network survives cluster run changes')
        for suffix in ['-controller','-watchdog','-internal']:
            self.assertLessEqual(len(prefix+suffix),63)
        for root in ['cluster','controller']:
            text=(ROOT/'terraform'/root/'main.tf').read_text()
            self.assertIn('evaluation-owner = var.owner_id',text)
            self.assertIn('evaluation-run = var.run_id',text)
    def test_network_policy_enforcement_is_explicit(self):
        text=(ROOT/'terraform/cluster/main.tf').read_text()
        self.assertRegex(text,r'datapath_provider\s*=\s*"LEGACY_DATAPATH"')
        self.assertRegex(text,r'network_policy\s*\{\s*enabled\s*=\s*true\s*provider\s*=\s*"CALICO"')
        self.assertRegex(text,r'network_policy_config\s*\{\s*disabled\s*=\s*false')

class ControllerCapacityTests(unittest.TestCase):
    def test_controller_capacity_and_tempfiles_use_persistent_boot_disk(self):
        text=(ROOT/'terraform/controller/main.tf').read_text()
        self.assertRegex(text,r'size\s*=\s*300')
        startup=(ROOT/'controller/startup.sh').read_text()
        self.assertIn('TMPDIR=/data/tmp',startup)
        self.assertIn('/data/{tmp,home,gcloud}',startup)
