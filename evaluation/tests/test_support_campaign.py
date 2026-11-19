import tempfile
import unittest
from pathlib import Path
from evaluation.gke.models import sha
from evaluation.gke import thesis_support


class CampaignMetadataTests(unittest.TestCase):
    def fixture(self, root):
        archive=root/'window.tar.gz';archive.write_bytes(b'archived evidence')
        prefix='gs://evidence/gke-qualification-v1/runs/new-full'
        receipt={'objects':[{'uri':prefix+'/windows/a/window.tar.gz','path':str(archive),'sha256':sha(archive),'bytes':archive.stat().st_size}]}
        readback={'seal_sha256':'a'*64,'objects':[{'uri':prefix+'/final-summary/GKE-SHA256SUMS','sha256':'a'*64}]}
        return receipt,readback

    def test_primary_metadata_has_no_complement_and_retains_provenance(self):
        with tempfile.TemporaryDirectory() as d:
            receipt,readback=self.fixture(Path(d))
            result=thesis_support.campaign_metadata('new-full','9'*40,'primary',receipt,readback)
            self.assertEqual(result['original_run'],'new-full')
            self.assertEqual(result['campaign']['role'],'primary')
            self.assertEqual(result['runtime_frozen_commit'],'9'*40)
            self.assertEqual(result['campaign']['runtime_commit_evidence'],'caller-supplied; not runtime attestation')
            self.assertNotIn('complement',result)

    def test_old_archives_cannot_be_relabeled(self):
        with tempfile.TemporaryDirectory() as d:
            receipt,readback=self.fixture(Path(d))
            receipt['objects'][0]['uri']=receipt['objects'][0]['uri'].replace('/new-full/','/old-full/')
            with self.assertRaisesRegex(ValueError,'campaign'):
                thesis_support.campaign_metadata('new-full','9'*40,'primary',receipt,readback)

    def test_readback_identity_and_hash_are_required(self):
        with tempfile.TemporaryDirectory() as d:
            receipt,readback=self.fixture(Path(d))
            for bad in ({'seal_sha256':'a'*64},dict(readback,objects=[{'uri':readback['objects'][0]['uri'],'sha256':'b'*64}])):
                with self.assertRaisesRegex(ValueError,'readback'):
                    thesis_support.campaign_metadata('new-full','9'*40,'primary',receipt,bad)

    def test_changed_archive_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            receipt,readback=self.fixture(Path(d))
            Path(receipt['objects'][0]['path']).write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'archive'):
                thesis_support.campaign_metadata('new-full','9'*40,'primary',receipt,readback)

    def test_partial_metadata_rejected_and_legacy_unchanged(self):
        legacy=thesis_support.campaign_metadata(None,None,None,{}, {})
        self.assertEqual(legacy['original_run'],'full-bounded-study-20261117')
        self.assertEqual(legacy['complement']['state'],'preparing')
        with self.assertRaises(ValueError):
            thesis_support.campaign_metadata('new-full',None,'primary',{}, {})
