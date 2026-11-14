import json
import tempfile
import unittest
from pathlib import Path


class LocalQualificationCommandTests(unittest.TestCase):
    def test_missing_live_records_remain_insufficient(self):
        from evaluation.gke import local_qualification as q
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            result=q.import_kind_records(root/'absent',root,{'karthos_revision':'a'*40,'karthos_source_hash':'b'*64})
            self.assertFalse(result['complete'])
            self.assertEqual(set(result['missing']),{'product-pin.json','structure.json','arbitration.json','placement-retained.json'})
            self.assertFalse((root/'structure.json').exists())

    def test_foreign_product_records_rejected(self):
        from evaluation.gke import local_qualification as q
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';source.mkdir();out=root/'out';out.mkdir()
            (source/'product-pin.json').write_text(json.dumps({'karthos_revision':'c'*40,'karthos_source_hash':'b'*64}))
            with self.assertRaisesRegex(ValueError,'product'):
                q.import_kind_records(source,out,{'karthos_revision':'a'*40,'karthos_source_hash':'b'*64})

    def test_receipt_refuses_missing_helpers_or_wrong_reset_assertions(self):
        from evaluation.gke import local_qualification as q
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'temporal-observations.json').write_text('[]')
            (root/'temporal-reset-expiry.log').write_text('ok')
            self.assertFalse(q.assemble_temporal(root))
            self.assertFalse((root/'temporal-history.json').exists())

    def test_handshake_parser_rejects_non_loopback_endpoint(self):
        from evaluation.gke import local_qualification as q
        with self.assertRaisesRegex(ValueError,'loopback'):
            q.validate_handshake({'endpoint':'http://example.org:8080','before':'a','after':'b'})

if __name__=='__main__':unittest.main()
