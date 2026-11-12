import unittest
from evaluation import semantics_pilot as s
from evaluation.pilot import metric_queries


class SemanticsPilotTests(unittest.TestCase):
    def test_matrix_is_smoke_all_cells_then_heavy_all_cells(self):
        schedule = s.schedule()
        self.assertEqual(16, len(schedule))
        self.assertEqual([60]*8+[300]*8, [x['spec']['duration_seconds'] for x in schedule])
        self.assertEqual(4, len({x['cell'].slug for x in schedule[:8]}))
        self.assertEqual(['k6','locust','locust','k6'], [x['spec']['generator'] for x in schedule[:4]])
        self.assertTrue(all(x['spec']['protocol_version']==3 for x in schedule))

    def test_quiet_requires_fresh_distinct_samples_and_full_identity(self):
        rows=[dict(observed_at=0,values={'a':2},sample_times={'a':0}),
              dict(observed_at=15,values={'a':2},sample_times={'a':0})]
        self.assertFalse(s.quiet_pair(rows,15))
        rows[-1]['sample_times']['a']=15
        self.assertTrue(s.quiet_pair(rows,15))
        rows[-1]['values']={'b':2}
        self.assertFalse(s.quiet_pair(rows,15))
        self.assertFalse(s.quiet_pair([],15))

    def test_source_is_only_applied_to_servicegraph_queries(self):
        for _, route, params, _ in metric_queries('a','b',60,source='mesh.istio'):
            if params.get('metric_name','').startswith('servicegraph.'):
                self.assertEqual('mesh.istio',params.get('source'))
            else:self.assertNotIn('source',params)

    def test_evidence_gate_does_not_require_numerical_pass(self):
        self.assertTrue(s.evidence_valid(dict(oracle_valid=True,source_valid=True,state='failed',
            generator={'artifact_complete':True}),derivation_valid=True))
        self.assertFalse(s.evidence_valid(dict(oracle_valid=True,source_valid=False),True))
        self.assertFalse(s.evidence_valid(dict(oracle_valid=True,source_valid=True),False))

    def test_counter_snapshot_tracks_real_sample_times_and_measured_interval(self):
        rows=[dict(metric={'edge_server':'checkout','source':'mesh.istio'},values=[3,3,3],timestamps=[1000,16000,31000])]
        snap=s.counter_snapshot(rows,32)
        self.assertEqual(15,snap['measured_collection_interval_seconds'])
        self.assertEqual([31],list(snap['sample_times'].values()))
        self.assertEqual([3],list(snap['values'].values()))

    def test_deployment_lock_rejects_wrong_revision_or_mutable_runtime(self):
        good=[{'image':'registry/karthos-backend:eval-new','image_id':'containerd://sha256:abc'}]
        s.validate_platform_images(good,'eval-new')
        with self.assertRaises(RuntimeError):s.validate_platform_images(good,'eval-old')
        with self.assertRaises(RuntimeError):s.validate_platform_images([{'image':'registry/karthos-backend:eval-new','image_id':'latest'}],'eval-new')

    def test_derivation_gate_requires_version_and_source(self):
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)
            sample={'labels':{'source':'mesh.istio','edge_client':'frontend','edge_server':'checkout'},'derivation':{'id':'istio.request-errors','input_metric':'servicegraph.request.count','interpretation':'HTTP 500–599 for non-gRPC; nonzero gRPC status for RPCs; missing RPC status unknown'}}
            file=path/'api-error-count.json'
            file.write_text(json.dumps({'data':{'samples':[sample]}}))
            self.assertFalse(s.derivation_evidence(path,'mesh.istio'))
            sample['derivation']['version']='1'
            file.write_text(json.dumps({'data':{'samples':[sample]}}))
            self.assertTrue(s.derivation_evidence(path,'mesh.istio'))
            self.assertFalse(s.derivation_evidence(path,'traces.servicegraph'))

    def test_quiet_rejects_advancing_but_stale_samples(self):
        rows=[dict(observed_at=100,values={'a':2},sample_times={'a':1}),
              dict(observed_at=115,values={'a':2},sample_times={'a':16})]
        self.assertFalse(s.quiet_pair(rows,15))
        rows[0]['sample_times']['a']=55
        rows[1]['sample_times']['a']=70
        self.assertTrue(s.quiet_pair(rows,15))

    def test_parity_requests_always_disable_vm_cache(self):
        for params in [dict(start='a',end='b',step='15'),dict(time='a')]:
            self.assertEqual('1',s.vm_query_request('metric',**params)['nocache'])

    def test_derivation_gate_rejects_unselected_edge_or_wrong_contract(self):
        import json,tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)
            sample={'labels':{'source':'mesh.istio','edge_client':'frontend','edge_server':'payment'},
                    'derivation':{'id':'istio.request-errors','version':'1','input_metric':'servicegraph.request.count','interpretation':'HTTP 500–599 for non-gRPC; nonzero gRPC status for RPCs; missing RPC status unknown'}}
            file=path/'api-error-count.json'
            def save():file.write_text(json.dumps({'data':{'samples':[sample]}}))
            save();self.assertFalse(s.derivation_evidence(path,'mesh.istio'))
            sample['labels']['edge_server']='checkout';sample['derivation']['id']='wrong'
            save();self.assertFalse(s.derivation_evidence(path,'mesh.istio'))
            sample['derivation']['id']='istio.request-errors'
            save();self.assertTrue(s.derivation_evidence(path,'mesh.istio'))

    def test_absolute_deadline_never_extends_eight_hour_budget(self):
        from datetime import datetime,timezone
        now=datetime(2026,10,20,tzinfo=timezone.utc)
        self.assertEqual(60,s.resolve_study_deadline('2026-10-20T00:01:00Z',now,28800)[0])
        self.assertEqual(28800,s.resolve_study_deadline('2026-10-22T00:00:00Z',now,28800)[0])
        with self.assertRaises(ValueError):s.resolve_study_deadline('2026-10-19T00:01:00Z',now,28800)
        with self.assertRaises(ValueError):s.resolve_study_deadline('2026-10-22T00:00:00',now,28800)

    def test_v3_warmup_retains_quality_failure_but_stops_infrastructure_exit(self):
        import json,tempfile
        from pathlib import Path
        from unittest.mock import patch
        from evaluation.protocol import Cell
        cell=Cell('astronomy','trace',1)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'smoke'/cell.slug;path.mkdir(parents=True)
            def execute(job,namespace,folder,run_id,*args,**kwargs):
                folder.mkdir(parents=True,exist_ok=True)
                (folder/'ledger.ndjson').write_text(json.dumps({'outcome_ok':False})+'\n')
                return dict(verified=True,semantic_complete=True,workload_exit_code=99,
                            generator_pods={'items':[]})
            with patch('evaluation.generator_execution.execute_generator',side_effect=execute), \
                 patch('evaluation.pilot.cleanup_generator') as cleanup, \
                 patch('evaluation.pilot.lock_runtime_images'):
                result=s.run_warmup(cell,path,'study')
                self.assertEqual('quality_failed',result['state'])
                self.assertEqual(99,result['workload_exit_code'])
                self.assertEqual(1,result['unexpected_outcomes'])
                cleanup.assert_called_once()
                self.assertTrue(cleanup.call_args.args[-1])
            with patch('evaluation.generator_execution.execute_generator',return_value=dict(verified=True,semantic_complete=True,workload_exit_code=1)), \
                 patch('evaluation.pilot.cleanup_generator'), \
                 patch('evaluation.pilot.lock_runtime_images'):
                with self.assertRaises(RuntimeError):s.run_warmup(cell,path/'infra','other-study')

    def test_v3_warmup_invalid_artifacts_are_not_cleaned(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from evaluation.protocol import Cell
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'smoke'/'r1-astronomy-trace';path.mkdir(parents=True)
            with patch('evaluation.generator_execution.execute_generator',side_effect=RuntimeError('incomplete')), \
                 patch('evaluation.pilot.cleanup_generator') as cleanup:
                with self.assertRaises(RuntimeError):s.run_warmup(Cell('astronomy','trace',1),path,'study')
                self.assertFalse(cleanup.call_args.args[-1])
