import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from evaluation.gke import commit_build as build


class CommitBuildTests(unittest.TestCase):
    def test_checkout_uses_exact_commit_not_dirty_worktree(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t); repo=root/'repo';repo.mkdir()
            def git(*args):return subprocess.run(['git','-C',str(repo),*args],check=True,capture_output=True,text=True).stdout.strip()
            git('init');git('config','user.email','fixture@example.invalid');git('config','user.name','Fixture')
            (repo/'input').write_text('committed');git('add','.');git('commit','-m','fixture');rev=git('rev-parse','HEAD')
            (repo/'input').write_text('dirty')
            build.checkout(str(repo),rev,root/'out')
            self.assertEqual((root/'out/input').read_text(),'committed')
            with self.assertRaises(ValueError):build.checkout(str(repo),'main',root/'other')
            with self.assertRaises(FileExistsError):build.checkout(str(repo),rev,root/'out')

    def test_download_rejects_corruption_and_does_not_publish_partial(self):
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/'archive'
            with patch.object(build.urllib.request,'urlopen',return_value=io.BytesIO(b'bad')):
                with self.assertRaises(ValueError):build.download({'url':'https://example.invalid/a','sha256':'0'*64},path)
            self.assertFalse(path.exists())
            with patch.object(build.urllib.request,'urlopen',return_value=io.BytesIO(b'good')):
                build.download({'url':'https://example.invalid/a','sha256':hashlib.sha256(b'good').hexdigest()},path)
            self.assertEqual(path.read_bytes(),b'good')

    def test_archive_rejects_traversal_and_links(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            for name,link in [('../escape',False),('link',True)]:
                archive=root/'a.tar.gz'
                with tarfile.open(archive,'w:gz') as tar:
                    info=tarfile.TarInfo(name)
                    if link:info.type=tarfile.SYMTYPE;info.linkname='/etc/passwd'
                    else:info.size=1
                    tar.addfile(info,None if link else io.BytesIO(b'x'))
                with self.assertRaises(ValueError):build.extract(archive,root/'out')

    def test_official_inputs_are_standalone_and_public(self):
        root=Path(__file__).resolve().parents[2]
        inputs=build.frozen_inputs(root)
        self.assertEqual(inputs['pins']['karthos_revision'],'4c2861b539fcd24d6a458dc0fd545fbdda5470d5')
        self.assertNotIn('baseline',inputs)
        self.assertNotIn('karthos-backend:latest',inputs['image_lock'])
        self.assertEqual(inputs['baseline_config']['k6_rate_per_second'],2)
        self.assertEqual(inputs['thresholds']['request_relative_tolerance'],0.15)
        lock=json.loads((root/build.INPUT_DIR/'build-lock.json').read_text())
        for record in lock['downloads'].values():
            self.assertTrue(record['url'].startswith('https://'))
            self.assertRegex(record['sha256'],r'^[a-f0-9]{64}$')
        self.assertNotIn('/Users/',json.dumps(lock))
        self.assertNotIn('us-central1-docker.pkg.dev/karthos/',json.dumps(inputs['image_lock']))

    def test_publication_cannot_rebind_a_changed_local_image(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            receipt={'images':{name:{'tag':'example/'+name+':commit-abc','image_id':'sha256:expected'} for name in ('karthos-backend','karthos-frontend','controller','watchdog')}}
            path=root/'receipt.json';path.write_text(json.dumps(receipt))
            with patch.object(build,'_run',return_value=json.dumps([{'Id':'sha256:changed','RepoDigests':[]}])):
                with self.assertRaisesRegex(ValueError,'changed'):build.record_publication(path)
            self.assertFalse((root/'published-product-lock.json').exists())

    def test_preparation_inputs_do_not_read_a_historical_study(self):
        from evaluation.gke.prepare import preparation_inputs
        from evaluation.gke.models import COMPLETION_V2
        root=Path(__file__).resolve().parents[2]
        with patch('evaluation.gke.prepare.baseline_summary',side_effect=AssertionError('historical dependency')):
            directory,summary,pins,images,thresholds,settings=preparation_inputs(root,{},COMPLETION_V2)
        self.assertEqual(directory,root/build.INPUT_DIR)
        self.assertEqual(summary['scope'],'versioned experiment inputs; not prior-run evidence')
        self.assertNotIn('windows',summary)
        self.assertEqual(pins['karthos_revision'],'4c2861b539fcd24d6a458dc0fd545fbdda5470d5')
        self.assertEqual(settings['k6_rate_per_second'],2)

    def test_product_build_uses_pinned_bases_and_lockfile_install(self):
        bases={'node:20-alpine':'node@sha256:'+'1'*64,'nginx:alpine':'nginx@sha256:'+'2'*64}
        text=build.pinned_dockerfile('FROM node:20-alpine AS build\nRUN npm install\nFROM nginx:alpine\n',bases)
        self.assertIn('RUN npm ci',text)
        self.assertIn(bases['node:20-alpine'],text)
        with self.assertRaises(ValueError):build.pinned_dockerfile('FROM unpinned:latest\n',bases)

if __name__=='__main__':unittest.main()
