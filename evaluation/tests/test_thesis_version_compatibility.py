"""Version compatibility preserves original local and historical evidence."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import time
import unittest
from unittest.mock import Mock, patch

from evaluation.gke import models, setup, thesis
from evaluation.diagnostics.thesis_local import qualify

ROOT=Path(__file__).resolve().parents[2]


class ThesisVersionCompatibilityTests(unittest.TestCase):
    def receipt(self, directory, protocol=models.THESIS_V2):
        checks={}
        for name in thesis.LOCAL_CHECKS:
            path=directory/(name+'.fixture')
            path.write_text(name+' original local evidence\n')
            checks[name]={'state':'pass','reference':path.name,'sha256':models.sha(path)}
        return {'protocol':protocol,'revision':thesis.START_REVISION,
                'source_sha256':'a'*64,'checks':checks}

    def test_v3_reuses_identical_product_v2_receipt_without_relabeling(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            original=self.receipt(root)
            path=root/'local-contract.json'
            models.write(path,original)
            checksum=models.sha(path)
            receipt=models.read(path)
            validated=thesis.local_contract(receipt,{'karthos_revision':thesis.START_REVISION,
                'karthos_source_hash':'a'*64},directory=root)
            self.assertIs(validated,receipt)
            self.assertEqual(validated,original)
            self.assertEqual(validated['protocol'],models.THESIS_V2)
            self.assertEqual(models.sha(path),checksum)

    def test_reuse_rejects_changed_pins_failed_checks_unknown_version_and_changed_artifact(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            original=self.receipt(root)
            pins={'karthos_revision':thesis.START_REVISION,'karthos_source_hash':'a'*64}
            for key,value in (('karthos_revision','b'*40),('karthos_source_hash','b'*64)):
                with self.subTest(pin=key), self.assertRaisesRegex(ValueError,'incomplete'):
                    thesis.local_contract(original,dict(pins,**{key:value}),directory=root)
            for protocol in ('gke-qualification-v1','thesis-qualification-v5'):
                with self.subTest(protocol=protocol), self.assertRaisesRegex(ValueError,'incomplete'):
                    thesis.local_contract(dict(original,protocol=protocol),pins,directory=root)
            failed=deepcopy(original)
            failed['checks']['source-semantics']['state']='insufficient'
            with self.assertRaisesRegex(ValueError,'incomplete'):
                thesis.local_contract(failed,pins,directory=root)
            (root/original['checks']['source-semantics']['reference']).write_text('changed')
            with self.assertRaisesRegex(ValueError,'checksum mismatch'):
                thesis.local_contract(original,pins,directory=root)

    def test_new_diagnostic_receipt_uses_active_v4_and_leaves_existing_v2_receipt_untouched(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            original=self.receipt(root)
            path=root/'local-contract.json'
            models.write(path,original)
            checksum=models.sha(path)
            generated=qualify(root,{'karthos_revision':thesis.START_REVISION,'karthos_source_hash':'a'*64})
            self.assertEqual(generated['protocol'],models.THESIS)
            self.assertTrue(all(row['state']=='insufficient' for row in generated['checks'].values()))
            self.assertEqual(models.sha(path),checksum)

    def test_active_v4_accepts_known_local_contract_protocols_without_relabeling(self):
        for protocol in models.THESIS_SCHEDULES:
            with self.subTest(protocol=protocol), TemporaryDirectory() as directory:
                root=Path(directory)
                receipt=self.receipt(root,protocol)
                validated=thesis.local_contract(receipt,{'karthos_revision':thesis.START_REVISION,
                    'karthos_source_hash':'a'*64},directory=root)
                self.assertEqual(validated['protocol'],protocol)

    def test_claim_matrix_retains_both_schedule_and_original_local_receipt_protocol(self):
        for schedule in models.THESIS_SCHEDULES:
            with self.subTest(schedule=schedule):
                ctx=SimpleNamespace(manifest={'schedule':schedule,'matrix':[],
                    'product_pins':{'karthos_revision':thesis.START_REVISION},
                    'local_contract_evidence':{'protocol':models.THESIS_V2,'checks':{}}})
                report=thesis.claim_matrix(ctx,[],None,None)
                self.assertEqual(report['protocol'],schedule)
                self.assertEqual(report['local_contract_protocol'],models.THESIS_V2)
                self.assertTrue(all(row['verdict'] in ('insufficient','not-applicable') for row in report['claims']))
                if schedule==models.REPAIR:
                    self.assertTrue(all(row['verdict']=='not-applicable' for row in report['claims'] if row['claim']=='retained-replicas-and-health' or row['claim'].startswith('clean-rebuild')))

    def test_repair_defers_only_unplanned_source_pair(self):
        ctx=SimpleNamespace(manifest={'schedule':models.REPAIR,'matrix':[],
            'product_pins':{'karthos_revision':thesis.START_REVISION},'local_contract_evidence':{}})
        report=thesis.claim_matrix(ctx,[],None,None)
        claims={row['claim']:row for row in report['claims']}
        self.assertEqual(claims['source-independence-astronomy']['verdict'],'not-applicable')
        self.assertEqual(claims['source-independence-boutique']['verdict'],'insufficient')
        ctx.manifest['schedule']=models.THESIS
        claims={row['claim']:row for row in thesis.claim_matrix(ctx,[],None,None)['claims']}
        self.assertEqual(claims['source-independence-astronomy']['verdict'],'insufficient')

    def test_product_metric_contract_selected_for_all_thesis_versions(self):
        from evaluation import runner
        for schedule in models.THESIS_SCHEDULES:
            with self.subTest(schedule=schedule), TemporaryDirectory() as directory:
                root=Path(directory)
                ctx=SimpleNamespace(root=root,result=root,work=root,kubeconfig=root/'kubeconfig',
                    deadline=time.time()+3600,command=Mock(),kube=Mock(),json=Mock(),
                    config={'limits':{'cleanup_reserve_seconds':1200},'infrastructure':{}},
                    manifest={'schedule':schedule,'product_snapshot':str(root),'baseline_config':{},
                              'product_pins':{'karthos_revision':thesis.START_REVISION},'image_lock':{}})
                with patch.dict('os.environ',{'GKE_EVAL_PRODUCT_ROOT':str(root)}),setup.legacy_context(ctx):
                    self.assertTrue(runner.CONFIG['thesis_metric_contract'])

    def test_historical_schedules_keep_history_and_report_original_protocol(self):
        from evaluation.gke.run import execute_matrix
        from test_gke_run import FakeDriver
        for schedule in (models.THESIS_V2,models.THESIS_V3):
            with self.subTest(schedule=schedule), TemporaryDirectory() as directory:
                root=Path(directory)
                protocol=models.read(ROOT/'protocols'/schedule/'protocol.yaml')
                matrix=models.build_matrix(protocol,schedule=schedule)
                ctx=SimpleNamespace(result=root,deadline=time.time()+3600,event=Mock(),write=models.write,
                    config={'limits':{'cleanup_reserve_seconds':1200}},
                    manifest={'schedule':schedule,'matrix':matrix,'protocol_definition':protocol,
                        'product_pins':{'karthos_revision':thesis.START_REVISION},'readiness_check':{'required':True}})
                driver=FakeDriver()
                driver.thesis_history=Mock(return_value={'export':{'verified':True,'uri':'gs://fixture/history'},'continuation_safe':True})
                report=execute_matrix(ctx,driver,gate=lambda *_:{'ready':True})
                self.assertEqual(report['state'],'complete')
                self.assertEqual(report['protocol'],schedule)
                self.assertEqual(models.read(root/'claim-matrix.json')['protocol'],schedule)
                driver.thesis_history.assert_called_once_with(matrix[7])
