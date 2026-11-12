import gzip
import hashlib
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from evaluation.gke import export
from evaluation.gke.export import verify_archive_privacy


class ArchivePrivacyTests(unittest.TestCase):
    def archive(self, directory, member, payload):
        path = Path(directory)/'window.tar.gz'
        with tarfile.open(path, 'w:gz') as bundle:
            info = tarfile.TarInfo(member)
            info.size = len(payload)
            bundle.addfile(info, io.BytesIO(payload))
        return path

    def test_locust_native_input_type_comparison_is_static_code(self):
        with tempfile.TemporaryDirectory() as directory:
            body = b'<script>if("password"===e.type){render();}</script>\n'
            path = self.archive(directory, 'generator-artifacts/locust-report.html', body)
            result = verify_archive_privacy(path)
            self.assertEqual(result['uncompressed_bytes_inspected'], len(body))
            with tarfile.open(path) as bundle:
                self.assertEqual(bundle.extractfile('generator-artifacts/locust-report.html').read(), body)

    def test_locust_comparison_does_not_hide_real_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            for secret in (b'password="==secret"', b'Authorization: fixture-token',
                           b'http://fixture:secret@demo.invalid/'):
                with self.subTest(secret=secret):
                    body = b'<script>if("password"===e.type){};</script> '+secret+b'\n'
                    path = self.archive(directory, 'generator-artifacts/locust-report.html', body)
                    with self.assertRaisesRegex(ValueError, 'potential credential'):
                        verify_archive_privacy(path)

    def test_record_limit_names_the_member_without_echoing_its_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = self.archive(directory, 'large.ndjson', b'fixture-secret' * 10 + b'\n')
            with patch.object(export, 'MAX_ARCHIVE_LINE', 40):
                with self.assertRaisesRegex(ValueError, 'record byte bound exceeded: large.ndjson') as error:
                    verify_archive_privacy(archive)
            self.assertNotIn('fixture-secret', str(error.exception))

    def test_window_parts_preserve_all_members_under_the_expanded_privacy_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.mkdir()
            archives = root / 'archives'
            archives.mkdir()
            for name in ('one.ndjson.gz', 'two.ndjson.gz'):
                (source / name).write_bytes(gzip.compress(b'{}\n' * 500))
            projected = root / 'projection'
            with patch.object(export, 'MAX_ARCHIVE_BYTES', 2048):
                export.export_projection(source, projected)
                parts = export.pack_window_archives(projected, archives)
                self.assertGreater(len(parts), 1)
                files = {}
                for part in parts:
                    archive = archives / part['name']
                    self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), part['sha256'])
                    self.assertLessEqual(verify_archive_privacy(archive)['uncompressed_bytes_inspected'], 2048)
                    with tarfile.open(archive) as bundle:
                        for member in bundle:
                            self.assertNotIn(member.name, files)
                            files[member.name] = bundle.extractfile(member).read()
            self.assertEqual(files, {file.name: file.read_bytes() for file in projected.iterdir()})

    def test_clean_telemetry_skips_document_redaction(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = self.archive(directory, 'samples.ndjson', b'{"value":42,"resource":{"name":"demo"}}\n')
            with patch.object(export, 'redact_document', wraps=export.redact_document) as redact:
                verify_archive_privacy(archive)
            redact.assert_not_called()

    def test_fast_rejection_still_decodes_escaped_and_unicode_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            for payload in (br'{"p\u0061ssword":"fixture"}', '{"paſsword":"fixture"}'.encode(),
                            br'{"annot\u0061tions":{"kubectl.kubernetes.io/last-applied-configuration":"fixture"}}'):
                with self.subTest(payload=payload), self.assertRaises(ValueError):
                    verify_archive_privacy(self.archive(directory, 'samples.ndjson', payload+b'\n'))

    def test_privacy_checks_tokens_exposed_by_redaction_marker_removal(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(ValueError, 'potential credential'):
            verify_archive_privacy(self.archive(directory, 'app.log', b'pass<redacted>word=fixture\n'))

    def test_truncated_compressed_member_cannot_pass_inspection(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'metrics.ndjson.gz'
            path.write_bytes(gzip.compress(b'{"name":"request.count","value":1}\n')[:-5])
            with self.assertRaises((EOFError, OSError, ValueError)):
                export.verify_archive_privacy(path)


class ProjectionPrivacyTests(unittest.TestCase):
    def test_projects_decoded_json_strings_and_keeps_archive_compatible(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.mkdir()
            original = b'{"connection":"password=fixture\\nkeep","trace":"p\\u0061ssword=fixture","labels":{"http.status_code":"200"}}\n'
            (source / 'config.json').write_bytes(original)
            projection = root / 'projection'
            self.assertTrue(export.export_projection(source, projection)['valid'])
            self.assertEqual(json.loads((projection / 'config.json').read_bytes()), {
                'connection': 'password=<redacted>\nkeep',
                'trace': 'password=<redacted>',
                'labels': {'http.status_code': '200'},
            })
            change, = export.read(projection / 'EXPORT-PROJECTION.json')['redacted_files']
            self.assertEqual(change['original_sha256'], hashlib.sha256(original).hexdigest())
            self.assertEqual(change['export_sha256'], export.sha(projection / 'config.json'))
            self.assertEqual((source / 'config.json').read_bytes(), original)
            archives = root / 'archives'
            archives.mkdir()
            for part in export.pack_window_archives(projection, archives):
                verify_archive_privacy(archives / part['name'])

    def test_projects_json_lines_and_yaml_block_scalars_without_changing_dimensions(self):
        import json
        from evaluation import yamlio
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.mkdir()
            (source / 'samples.ndjson').write_text(
                '{"note":"Authorization: Bearer fixture","status":200}\n{"value":2}\n')
            (source / 'config.yaml').write_text('note: |\n  password="fixture quote"\n  kept\nstatus: 200\n')
            projection = root / 'projection'
            self.assertTrue(export.export_projection(source, projection)['valid'])
            self.assertEqual([json.loads(line) for line in (projection / 'samples.ndjson').read_text().splitlines()],
                             [{'note': 'Authorization: Bearer <redacted>', 'status': 200}, {'value': 2}])
            self.assertEqual(yamlio.load_mapping((projection / 'config.yaml').read_text()),
                             {'note': 'password="<redacted>"\nkept\n', 'status': 200})

    def test_loose_authorization_uses_the_same_policy_as_archive_members(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.mkdir()
            raw = b'Authorization: Bearer fixture-token\nAuthorization: Basic fixture-encoded==\n'
            (source / 'app.log').write_bytes(raw)
            incoming = root / 'incoming.tar.gz'
            with tarfile.open(incoming, 'w:gz') as archive:
                member = tarfile.TarInfo('app.log')
                member.size = len(raw)
                archive.addfile(member, io.BytesIO(raw))
            with self.assertRaisesRegex(ValueError, 'potential credential'):
                verify_archive_privacy(incoming)
            projection = root / 'projection'
            self.assertTrue(export.export_projection(source, projection)['valid'])
            self.assertEqual((projection / 'app.log').read_bytes(),
                             b'Authorization: Bearer <redacted>\nAuthorization: Basic <redacted>\n')
            archives = root / 'archives'
            archives.mkdir()
            for part in export.pack_window_archives(projection, archives):
                verify_archive_privacy(archives / part['name'])

    def test_projection_rejects_unsafe_or_malformed_evidence_before_seal(self):
        cases = (
            ('app.log', b'pass<redacted>word=fixture\n'),
            ('digest.log', b'Authorization: Digest username="operator", response="fixture"\n'),
            ('truncated.log', b'password="fixture\n'),
            ('null-byte.log', b'\x00opaque'),
            ('samples.ndjson', b'{"value":2}\n{broken\n'),
            ('manifest.json', b'{"password":"fixture","password":"<redacted>"}'),
            ('blob', b'\xffopaque'),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, (name, raw) in enumerate(cases):
                with self.subTest(name=name):
                    source = root / f'source-{index}'
                    source.mkdir()
                    (source / name).write_bytes(raw)
                    destination = root / f'projection-{index}'
                    with self.assertRaises(ValueError) as error:
                        export.export_projection(source, destination)
                    self.assertNotIn('fixture', str(error.exception))
                    self.assertFalse((destination / 'GKE-SHA256SUMS').exists())
                    self.assertEqual((source / name).read_bytes(), raw)

    def test_archive_detects_escaped_inline_credentials_in_json_strings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'samples.ndjson.gz'
            path.write_bytes(gzip.compress(br'{"note":"p\u0061ssword=fixture"}' + b'\n'))
            with self.assertRaisesRegex(ValueError, 'credential'):
                verify_archive_privacy(path)

    def test_archive_rejects_private_operational_files_even_when_the_payload_is_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'window.tar.gz'
            with tarfile.open(archive, 'w:gz') as bundle:
                bundle.addfile(tarfile.TarInfo('nested/kubeconfig'), io.BytesIO(b''))
            with self.assertRaisesRegex(ValueError, 'unsafe archived evidence path'):
                verify_archive_privacy(archive)
