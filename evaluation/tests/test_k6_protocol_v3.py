import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from evaluation.gke import models, readiness
from evaluation.gke.cli import parser

ROOT = Path(__file__).resolve().parents[2]


class K6ProtocolV3Tests(unittest.TestCase):
    def test_exact_readiness_order_and_limits(self):
        rows = readiness.matrix()
        self.assertEqual([(r['cell'], r['phase'], r['scenario']) for r in rows], [
            ('astronomy-mesh','smoke','matched'),
            ('boutique-mesh','smoke','matched'),
            ('boutique-mesh','arbitration','both-sources'),
            ('boutique-mesh','restart','checkout'),
            ('boutique-mesh','restart','gateway'),
            ('boutique-trace','arbitration','replanned-trace'),
            ('boutique-trace','smoke','matched'),
            ('boutique-trace','resource','network'),
            ('boutique-trace','resource','cpu-memory'),
            ('boutique-trace','resource','storage'),
            ('boutique-trace','restart','checkout'),
            ('boutique-trace','restart','gateway'),
            ('astronomy-trace','smoke','matched')])
        self.assertEqual(sum(r['duration_seconds'] for r in rows), 1020)
        for row in rows:
            self.assertEqual((row['generator'],row['rate'],row['execution']), ('k6',2,'serial'))
            self.assertEqual(row['duration_seconds'],300 if row['scenario']=='network' else 60)
        self.assertEqual((readiness.VERSION,readiness.SECONDS,readiness.RESERVE),(3,10800,1200))

    def test_historical_v3_and_current_cli_remain_k6_only(self):
        protocol=models.read(ROOT/'protocols'/models.THESIS_V3/'protocol.yaml')
        self.assertEqual(models.THESIS_V3,'thesis-qualification-v3')
        self.assertEqual(protocol['generators'],['k6'])
        self.assertEqual(protocol['history_observation_seconds'],720)
        args=parser().parse_args(['prepare','--id','fixture'])
        self.assertEqual(args.schedule,models.COMPLETION_V2)
        with self.assertRaises(ValueError):
            readiness.validate_launch(SimpleNamespace(manifest={'schedule':models.THESIS_V2}))

    def test_historical_v3_rejects_generator_and_readiness_drift(self):
        from copy import deepcopy
        original=models.read(ROOT/'protocols'/models.THESIS_V3/'protocol.yaml')
        for key,value in (('generators',['k6','locust']),('readiness_definition_version',1),('readiness_definition_version',2),
                          ('readiness_cases',17),('readiness_load_seconds',1260)):
            with self.subTest(key=key):
                changed=deepcopy(original)
                changed[key]=value
                with self.assertRaisesRegex(ValueError,'k6-only readiness'):
                    models.build_matrix(changed,schedule=models.THESIS_V3)

    def test_definition_path_keeps_legacy_contracts(self):
        for schedule in models.THESIS_SCHEDULES:
            ctx=SimpleNamespace(root=ROOT,manifest={'schedule':schedule})
            self.assertEqual(models.definition_path(ctx,'coverage.yaml'),ROOT/'protocols'/schedule/'coverage.yaml')

    def test_legacy_analysis_preserves_original_input_and_verifier(self):
        from evaluation.gke.analysis import analyse
        for protocol in ('gke-qualification-v1',models.THESIS_V2):
            with self.subTest(protocol=protocol), tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                source=root/'results/gke-qualification-v1/runs/legacy'
                source.mkdir(parents=True)
                models.write(source/'run-report.json',{'protocol':protocol,'state':'complete','windows':[]})
                if protocol==models.THESIS_V2:
                    models.write(source/'claim-matrix.json',{'protocol':protocol,'claims':[]})
                original=models.sha(source/'run-report.json')
                analyse(root,'legacy','new-analysis')
                report=models.read(root/'analyses/gke-qualification-v1/legacy/new-analysis/analysis.json')
                self.assertEqual(models.sha(source/'run-report.json'),original)
                self.assertTrue(report['verifier_version'].startswith(protocol))
                self.assertFalse(report['original_results_modified'])
