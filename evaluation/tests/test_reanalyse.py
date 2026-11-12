import json
import tempfile
import unittest
from pathlib import Path
from evaluation.reanalyse import reanalyse, hashes


class ReanalysisTests(unittest.TestCase):
    def test_new_analysis_keeps_source_immutable_and_marks_missing_new_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'original';source.mkdir()
            (source/'manifest.json').write_text(json.dumps(dict(start=0,end=10,run_id='r')))
            before=hashes(source)
            output=Path(directory)/'analysis'
            manifest=reanalyse(source,output)
            self.assertEqual(hashes(source),before)
            self.assertEqual(manifest['source_sha256'],before)
            self.assertEqual(manifest['protocol_version'],3)
            report=json.loads((output/'window/verification.json').read_text())
            self.assertEqual(report['new_protocol_evidence']['state'],'incomplete')
            with self.assertRaises(ValueError):reanalyse(source,output)
            with self.assertRaises(ValueError):reanalyse(source,source/'analysis')


class Protocol4ReanalysisTests(unittest.TestCase):
    def test_explicit_protocol4_preserves_old_files_and_records_missing_boundary_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'original';source.mkdir()
            (source/'manifest.json').write_text(json.dumps(dict(start=1,end=10,run_id='r',protocol_version=3)))
            before=hashes(source)
            output=Path(directory)/'analysis'
            manifest=reanalyse(source,output,protocol_version=4)
            self.assertEqual(hashes(source),before)
            self.assertEqual(manifest['protocol_version'],4)
            self.assertEqual(manifest['verifier_version'],'4.0.0')
            report=json.loads((output/'window/verification.json').read_text())
            self.assertEqual(report['new_protocol_evidence']['state'],'incomplete')
            self.assertIn('structured/agent/raw/receiver-input.ndjson',report['new_protocol_evidence']['missing'])
            self.assertFalse(report['oracle_valid'])
