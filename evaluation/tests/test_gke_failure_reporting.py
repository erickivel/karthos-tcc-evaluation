"""Pure contracts for measurement truth versus continuation/export failures."""
import unittest
from evaluation.gke import recoverable_run as recovery

class FailureReportingTests(unittest.TestCase):
    def test_safety_failure_preserves_completed_measurement(self):
        spec={'id':'smoke-a1','logical_id':'smoke'}
        report={'path':'/retained/smoke-a1','execution':{'state':'complete'},'evidence':{'valid':True},
                'numerical_agreement':{'state':'insufficient'},'traffic':{'report':{'generator_contract':{'retained_generator':{'unexpected_outcomes':0}}}}}
        row=recovery.attempt_record(spec,report,failure={'kind':'SafetyFailure','message':'namespace owner mismatch'},barrier={'safe':False},receipt={'verified':False})
        self.assertEqual(row['execution'],'complete')
        self.assertTrue(row['evidence_valid'])
        self.assertFalse(row['continuation_safe'])
        self.assertFalse(row['evidence_export_verified'])
        self.assertEqual(row['state'],'failed')
        self.assertEqual(row['path'],'/retained/smoke-a1')
        self.assertEqual(row['application_outcomes'],'no unexpected outcomes')

    def test_unexported_local_result_does_not_hide_verified_retry(self):
        first={'id':'smoke','attempt_id':'smoke-a1','execution':'complete','evidence_valid':True,'state':'failed','export':{'verified':False},'numerical_agreement':'fail'}
        second={**first,'attempt_id':'smoke-a2','state':'complete','export':{'verified':True},'numerical_agreement':'pass'}
        row=recovery._row({'id':'smoke'},[first,second])
        self.assertEqual(row['representative_attempt'],'smoke-a2')
        self.assertEqual(len(row['attempts']),2)

    def test_verified_disagreement_is_not_replaced_by_later_pass(self):
        first={'id':'smoke','attempt_id':'smoke-a1','execution':'complete','evidence_valid':True,'state':'complete','export':{'verified':True},'numerical_agreement':'fail'}
        second={**first,'attempt_id':'smoke-a2','numerical_agreement':'pass'}
        self.assertEqual(recovery._row({'id':'smoke'},[first,second])['representative_attempt'],'smoke-a1')

    def test_missing_measurement_is_not_invented_as_complete(self):
        row=recovery.attempt_record({'id':'a1','logical_id':'a'},None,failure={'kind':'setup','message':'not deployed'})
        self.assertEqual(row['execution'],'unattempted')
        self.assertFalse(row['evidence_valid'])
        self.assertEqual(row['state'],'failed')

    def test_real_runner_keeps_workload_truth_after_failed_transition(self):
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch, Mock
        from tempfile import TemporaryDirectory
        import time
        from evaluation.gke.models import write
        with TemporaryDirectory() as temporary:
            root=Path(temporary)
            spec={'id':'r1-smoke-boutique-trace-k6-matched','repeat':1,'phase':'smoke','cell':'boutique-trace','demo':'boutique','source':'trace','generator':'k6','scenario':'matched','duration_seconds':60,'rate':2}
            attempt=spec['id']+'-a1';window=root/'window';window.mkdir()
            measured={'path':str(window),'execution':{'state':'complete'},'evidence':{'valid':True},'numerical_agreement':{'state':'insufficient'},'traffic':{'report':{'generator_contract':{'retained_generator':{'unexpected_outcomes':0}}}}}
            ctx=SimpleNamespace(result=root,run_id='unit-check',manifest={'matrix':[spec]},config={'execution_segment':'thesis','limits':{'cleanup_reserve_seconds':10}},deadline=time.time()+3600,state={'resources':[]},write=write,event=lambda *a,**k:None)
            driver=SimpleNamespace(paths={attempt:window},nodes=[],start_repeat=Mock(),setup=Mock(),window=Mock(return_value=measured),upload=Mock(),finish=Mock(return_value={'cluster_cleanup':{'residual':[]}}))
            class Diagnostics:
                def __init__(self,ctx,path,**kwargs):self.path=path
                def start(self):self.path.mkdir(parents=True)
                def stop(self):return {'stopped':True}
            with patch('evaluation.gke.execution_diagnostics.ExecutionDiagnostics',Diagnostics),patch('evaluation.gke.minimal_readiness.check_cell',return_value={'usable':True,'cell':'boutique-trace'}),patch('evaluation.gke.provision.require_context'),patch('evaluation.gke.provision.assert_project_lease'),patch.object(recovery,'transition',return_value={'safe':False,'error':{'message':'wrong namespace contract'}}),patch.object(recovery,'_checkpoint',return_value={'verified':True}),patch('evaluation.gke.thesis.claim_matrix',return_value={'claims':[]}):
                result=recovery.execute(ctx,driver)
            row=result['records'][0]
            self.assertEqual(row['execution'],'complete')
            self.assertTrue(row['evidence_valid'])
            self.assertFalse(row['continuation_safe'])
            self.assertFalse(row['evidence_export_verified'])
            self.assertEqual(row['path'],str(window))
            driver.upload.assert_not_called()
