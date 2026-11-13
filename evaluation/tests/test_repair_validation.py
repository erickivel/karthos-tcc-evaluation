import copy,json,unittest
from pathlib import Path
from evaluation.gke import models,evaluate
from evaluation.gke.recoverable_run import planning_forecast

ROOT=Path(__file__).resolve().parents[2]
REPAIR='thesis-repair-validation-v1'

class RepairValidationTests(unittest.TestCase):
    def protocol(self):
        p=json.loads((ROOT/'protocols/thesis-qualification-v4/protocol.yaml').read_text())
        p.update(id=REPAIR,repetitions=1,rebuild_cluster=False,history_observation_seconds=0,
                 cluster_cell_order=[['boutique-trace','astronomy-mesh','boutique-mesh']],
                 origin_protocol='thesis-qualification-v4')
        return p

    def test_six_frozen_windows_and_origin_ids(self):
        rows=models.build_matrix(self.protocol(),schedule=REPAIR)
        self.assertEqual([(r['cell'],r['phase']) for r in rows],[(cell,phase) for cell in ['boutique-trace','astronomy-mesh','boutique-mesh'] for phase in ['smoke','baseline']])
        self.assertEqual(sum(r['duration_seconds'] for r in rows),1080)
        self.assertEqual({r['repeat'] for r in rows},{1})
        self.assertEqual(rows[0]['origin_id'],'r2-smoke-boutique-trace-k6-matched')
        self.assertEqual(rows[2]['origin_id'],'r1-smoke-astronomy-mesh-k6-matched')
        self.assertEqual(len(models.build_matrix(json.loads((ROOT/'protocols/thesis-qualification-v4/protocol.yaml').read_text()),schedule=models.THESIS)),21)

    def test_frozen_inputs_cannot_be_changed(self):
        for key,value in [('baseline_rate',4),('history_observation_seconds',720),('rebuild_cluster',True),('repetitions',2),('cluster_cell_order',[['boutique-mesh']])]:
            p=self.protocol();p[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):models.build_matrix(p,schedule=REPAIR)

    def test_default_false_only_for_sum_and_temporality_unknown(self):
        self.assertEqual(evaluate._instrument_contract({'sum':{}},'sum'),{'kind':'sum','is_monotonic':False,'aggregation_temporality':None})
        self.assertEqual(evaluate._instrument_contract({'gauge':{}},'gauge'),{'kind':'gauge','is_monotonic':None,'aggregation_temporality':None})
        self.assertEqual(evaluate._instrument_contract({'sum':{'isMonotonic':True,'aggregationTemporality':2}},'sum')['is_monotonic'],True)

    def test_numerical_scope_excludes_catalog_and_traffic(self):
        selected=[{'name':'system.memory.usage'},{'name':'servicegraph.request.count'}]
        raw=[{'metric':'unselected','state':'insufficient'},{'metric':'system.memory.usage','state':'pass'}]
        result=evaluate._numerical_scope(raw,selected,{'state':'pass'},'container.cpu.usage','container.memory.usage')
        self.assertEqual(result['state'],'pass')
        self.assertEqual(result['counts'],{'eligible':2,'pass':2,'fail':0,'insufficient':0,'not-applicable':0})
        self.assertFalse(any(c.get('metric')=='servicegraph.request.count' for c in result['checks']))
        result=evaluate._numerical_scope([],selected,{'state':'incomplete'},'container.cpu.usage','container.memory.usage')
        self.assertEqual(result['state'],'insufficient')
        self.assertEqual(result['counts']['insufficient'],2)

    def test_terminal_rejection_requires_authoritative_request_and_no_operation(self):
        from evaluation.gke.provision import _terminal_cluster_rejection
        name='projects/p/locations/us-central1-b/clusters/test'
        intent={'kind':'cluster','name':name,'registered_at':100,'expected_creator':'controller@example.invalid','intent_id':'a'*32}
        event={'insertId':'audit-1','timestamp':'1970-01-01T00:01:41Z','protoPayload':{'methodName':'google.container.v1.ClusterManager.CreateCluster','serviceName':'container.googleapis.com','resourceName':name.replace('/locations/','/zones/'),'authenticationInfo':{'principalEmail':'controller@example.invalid'},'status':{'code':3},'response':{'@type':'Operation'}}}
        self.assertEqual(_terminal_cluster_rejection(intent,[event],[])['audit_insert_id'],'audit-1')
        self.assertIsNone(_terminal_cluster_rejection(intent,[event],[{'operationType':'CREATE_CLUSTER','targetLink':name,'startTime':'1970-01-01T00:01:40Z'}]))
        for change in ({'registered_at':102},{'observed_uid':'uid'},{'expected_creator':'other@example.invalid'},{'expected_creator':None}):
            self.assertIsNone(_terminal_cluster_rejection(dict(intent,**change),[event],[]))
        for code in (0,4,13):
            import copy
            row=copy.deepcopy(event);row['protoPayload']['status']['code']=code
            self.assertIsNone(_terminal_cluster_rejection(intent,[row],[]))

    def test_rejection_reconciliation_keeps_uncertain_resources(self):
        import tempfile,json
        from types import SimpleNamespace
        from unittest.mock import patch
        from evaluation.gke import provision
        with tempfile.TemporaryDirectory() as directory:
            intent={'kind':'cluster','name':'projects/p/locations/z/clusters/x','registered_at':100,'expected_creator':'controller@example.invalid','intent_id':'a'*32}
            ctx=SimpleNamespace(config={'gcp':{'project_id':'p','zone':'z'}},result=Path(directory),event=lambda *args,**kw:None)
            with patch.object(provision,'doc',side_effect=RuntimeError('403')):
                self.assertIsNone(provision._reconcile_rejected_cluster(ctx,intent))
            for audits,operations,present in (([],[],None),([{}]*100,[],None)):
                with patch.object(provision,'doc',side_effect=[audits,operations]),patch.object(provision,'cloud_get',return_value=present):
                    self.assertIsNone(provision._reconcile_rejected_cluster(ctx,intent))
            proof={'audit_insert_id':'evidence'}
            with patch.object(provision,'doc',side_effect=[[],[]]),patch.object(provision,'_terminal_cluster_rejection',return_value=proof),patch.object(provision,'cloud_get',return_value={'uid':'present'}):
                self.assertIsNone(provision._reconcile_rejected_cluster(ctx,intent))
            with patch.object(provision,'doc',side_effect=[[],[]]),patch.object(provision,'_terminal_cluster_rejection',return_value=proof),patch.object(provision,'cloud_get',return_value=None):
                self.assertEqual(provision._reconcile_rejected_cluster(ctx,intent),proof)
                self.assertTrue((Path(directory)/'infrastructure/rejected-creations'/intent['intent_id']/'resolution.json').exists())

    def test_observer_continues_after_recovery_and_until_cleanup(self):
        from evaluation.gke.monitor import classify
        failed={'matrix':[{'id':'window','attempts':[{'attempt_id':'window-a1','state':'failed'}]}]}
        first=classify(failed)
        self.assertTrue(first['new_failures'])
        recovered=classify({'matrix':[]},first['seen_failures'])
        self.assertEqual(recovered['new_failures'],[])
        self.assertTrue(recovered['keep_observing'])
        self.assertTrue(classify({'final_seal_verified':True})['keep_observing'])
        self.assertFalse(classify({'final_seal_verified':True,'temporary_removal_confirmed':True})['keep_observing'])

    def test_origin_is_checked_without_importing_passes(self):
        import tempfile
        from evaluation.gke.repair import origin_evidence
        from evaluation.gke.models import write,sha
        with tempfile.TemporaryDirectory() as directory:
            directory=Path(directory);report=directory/'run-report.json';seal=directory/'GKE-SHA256SUMS'
            rows=models.build_matrix(self.protocol(),schedule=REPAIR)
            write(report,{'protocol':models.THESIS,'records':[{'id':row['origin_id'],'state':'failed' if row['cell']=='astronomy-mesh' else 'unattempted'} for row in rows]})
            seal.write_text(sha(report)+'  run-report.json\n')
            receipt=origin_evidence(report,seal,'gs://evidence/gke-qualification-v1/runs/old/exports/final/run-report.json',rows)
            self.assertEqual(receipt['run_id'],'old')
            self.assertNotIn('records',receipt)
            report.write_text('{}')
            with self.assertRaisesRegex(ValueError,'seal mismatch'):origin_evidence(report,seal,receipt['report_uri'],rows)

    def test_repair_forecast_omits_history_and_rebuild(self):
        rows=models.build_matrix(self.protocol(),schedule=REPAIR)
        c={'readiness':{'phase_estimates_seconds':{key:10 for key in ['provision','rebuild','setup','capture_setup_restore','export','history','retry_reserve']}},'limits':{'cleanup_reserve_seconds':1200,'infrastructure_seconds':43200}}
        report=planning_forecast(c,rows)
        self.assertEqual(report['rebuild_seconds'],0)
        self.assertEqual(report['history_seconds'],0)
        self.assertEqual(report['launch_windows'],6)

if __name__=='__main__':unittest.main()
