import copy
import unittest
from pathlib import Path
from evaluation.gke import models

ROOT = Path(__file__).resolve().parents[2]
SCHEDULE = 'thesis-completion-v1'

class CompletionScheduleTests(unittest.TestCase):
    def protocol(self):
        return models.read(ROOT/'protocols'/SCHEDULE/'protocol.yaml')

    def test_ten_windows_in_reviewed_order_with_history_marker(self):
        rows=models.build_matrix(self.protocol(),schedule=SCHEDULE)
        self.assertEqual([(r['cell'],r['phase']) for r in rows[:8]],[(cell,phase) for cell in
            ['boutique-trace','astronomy-trace','astronomy-mesh','boutique-mesh'] for phase in ['smoke','baseline']])
        self.assertEqual([(r['scenario'],r['duration_seconds']) for r in rows[8:]], [('both-sources',60),('replanned-trace',60)])
        self.assertEqual(sum(r['duration_seconds'] for r in rows),1560)
        self.assertEqual({(r['repeat'],r['generator'],r['rate'],r['execution']) for r in rows},{(1,'k6',2,'serial')})
        self.assertEqual([r['id'] for r in rows if r.get('history_observation')],[rows[7]['id']])
        self.assertEqual(models.validate_schedule({'schedule':SCHEDULE,'protocol_definition':self.protocol(),'matrix':rows}),SCHEDULE)

    def test_mutated_workloads_and_claim_scope_are_rejected(self):
        for key,value in [('baseline_rate',3),('history_observation_seconds',0),('arbitration_seconds',300),
                          ('repetitions',2),('rebuild_cluster',True),('generators',['locust']),
                          ('servicegraph_store_max_items',1000),('export_horizon_version',None),('attempt_policy',{})]:
            p=self.protocol();p[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):models.build_matrix(p,schedule=SCHEDULE)

    def test_cli_and_isolated_history_namespace(self):
        from evaluation.gke.cli import parser
        args=parser().parse_args(['prepare','--id','test','--schedule',SCHEDULE])
        self.assertEqual(args.schedule,SCHEDULE)
        from evaluation.gke import thesis_history
        import inspect
        self.assertIn('RECOVERABLE_SCHEDULES',inspect.getsource(thesis_history.capture))

    def test_historical_schedules_stay_unchanged(self):
        for schedule,count in [('thesis-repair-validation-v2',6),('thesis-qualification-v4',21)]:
            self.assertEqual(len(models.build_matrix(models.read(ROOT/'protocols'/schedule/'protocol.yaml'),schedule=schedule)),count)

    def test_preparation_rejects_inherited_partial_segment(self):
        import tempfile,json
        from evaluation.gke.prepare import prepare
        config=models.read(ROOT/'config/gke.completion.example.yaml')
        config['execution_segment']='thesis'
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.json';path.write_text(json.dumps(config))
            with self.assertRaisesRegex(ValueError,'all ten windows'):
                prepare(ROOT,path,'never-created',schedule=SCHEDULE)

    def test_completion_budget_amendment_is_scoped_and_enforced(self):
        from evaluation.gke.repair import validate_budget
        c=models.read(ROOT/'config/gke.completion.example.yaml')
        self.assertEqual(c['limits']['gross_limit_usd'],150)
        models.validate_config(c,schedule=SCHEDULE)
        with self.assertRaises(ValueError):models.validate_config(c,schedule=models.REPAIR_V2)
        budget={'gross_limit_usd':150,'prior_runtime_allowance_usd':73,'combined_estimate_usd':149,
                'shared_allowances_cumulative':True,'basis':'conservative gross reporting reserve'}
        manifest={'schedule':SCHEDULE,'config':{'limits':{'gross_limit_usd':150},'readiness':{'study_budget':budget}},
                  'study_budget':budget,'cost_preview':{'gross_estimate':76}}
        validate_budget(manifest)
        for cap,total in [(100,149),(150,151),(200,149)]:
            changed=copy.deepcopy(manifest);changed['study_budget'].update(gross_limit_usd=cap,combined_estimate_usd=total)
            changed['config']['readiness']['study_budget']=changed['study_budget']
            with self.subTest(cap=cap,total=total),self.assertRaises(ValueError):validate_budget(changed)
