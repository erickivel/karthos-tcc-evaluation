import copy
import unittest
from pathlib import Path
from evaluation.gke import models

ROOT=Path(__file__).resolve().parents[2]
V2='thesis-completion-v2'

class OfficialProtocolTests(unittest.TestCase):
    def protocol(self):
        return models.read(ROOT/'protocols'/V2/'protocol.yaml')

    def test_official_schedule_is_registered(self):
        self.assertIn(V2, models.RECOVERABLE_SCHEDULES)
        self.assertIn(V2, models.CAPTURE_V2_SCHEDULES)

    def test_matrix_has_two_clean_clusters_and_arbitration_before_rebuild(self):
        rows=models.build_matrix(self.protocol(),schedule=V2)
        self.assertEqual(len(rows),14)
        self.assertEqual(sum(r['duration_seconds'] for r in rows),2280)
        self.assertEqual([(r['repeat'],r['cell'],r['phase']) for r in rows[:8]],
            [(1,c,p) for c in ('boutique-trace','astronomy-trace','astronomy-mesh','boutique-mesh') for p in ('smoke','baseline')])
        self.assertEqual([r['scenario'] for r in rows[8:10]],['both-sources','replanned-trace'])
        self.assertEqual([(r['repeat'],r['cell'],r['phase']) for r in rows[10:]],
            [(2,c,p) for c in ('boutique-mesh','boutique-trace') for p in ('smoke','baseline')])
        self.assertEqual([r['id'] for r in rows if r.get('history_observation')],[rows[7]['id']])
        self.assertEqual(len({r['id'] for r in rows}),14)
        self.assertTrue(all(r['rate']==2 and r['generator']=='k6' for r in rows))
        models.validate_schedule({'schedule':V2,'protocol_definition':self.protocol(),'matrix':rows})

    def test_frozen_contract_rejects_mutations(self):
        for key,value in [('repetitions',1),('rebuild_cluster',False),('baseline_seconds',60),('servicegraph_store_max_items',1000)]:
            p=self.protocol();p[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):models.build_matrix(p,schedule=V2)

    def test_new_cli_defaults_and_historical_definitions(self):
        from evaluation.gke.cli import parser
        args=parser().parse_args(['prepare','--id','example'])
        self.assertEqual(args.schedule,V2)
        self.assertEqual(args.config,'config/gke.completion-v2.example.yaml')
        for schedule,count in [('thesis-completion-v1',10),('thesis-repair-validation-v2',6),('thesis-qualification-v4',21)]:
            self.assertEqual(len(models.build_matrix(models.read(ROOT/'protocols'/schedule/'protocol.yaml'),schedule=schedule)),count)

    def test_complete_forecast_includes_rebuild_and_exceeds_old_limit(self):
        from evaluation.gke.recoverable_run import planning_forecast
        config=models.read(ROOT/'config/gke.completion-v2.example.yaml')
        models.validate_config(config,schedule=V2)
        config['readiness']['phase_estimates_seconds']={'provision':1200,'rebuild':1200,'setup':1200,'capture_setup_restore':300,'export':1200,'history':720,'retry_reserve':2400}
        forecast=planning_forecast(config,models.build_matrix(self.protocol(),schedule=V2))
        self.assertEqual(forecast['launch_windows'],14)
        self.assertGreater(forecast['rebuild_seconds'],0)
        self.assertGreater(forecast['seconds'],43200)
        self.assertTrue(forecast['fits'])

    def test_catalog_classification_covers_exactly_43_with_18_selected(self):
        coverage=models.read(ROOT/'protocols'/V2/'coverage.yaml')['entries']
        selected=models.read(ROOT/'protocols'/V2/'metrics.yaml')['metrics']
        self.assertEqual(len(coverage),43)
        self.assertEqual({x['name'] for x in coverage if x['accuracy_selected']},{x['name'] for x in selected})
        self.assertEqual(len(selected),18)

class RebuildSafetyTests(unittest.TestCase):
    def test_unresolved_first_cluster_blocks_new_creation(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from evaluation.gke import provision
        ctx=SimpleNamespace(manifest={'schedule':V2},state={'resources':[{'kind':'cluster','uid':'old','name':'old'}],'cluster_generation':1})
        with patch.object(provision,'require_context'),patch.object(provision,'assert_project_lease'), \
             patch('evaluation.gke.teardown.destroy_cluster',return_value={'residual':['old'],'unresolved_creation_intents':['old']}), \
             patch.object(provision,'cluster_variables') as variables:
            with self.assertRaisesRegex(RuntimeError,'rebuild.*cleanup'):
                provision.create_cluster(ctx,2,credentials=False)
            variables.assert_not_called()

class BuildCommandTests(unittest.TestCase):
    def test_build_command_requires_explicit_revision_and_destination(self):
        from evaluation.gke.cli import parser,dispatch
        from unittest.mock import patch
        args=parser().parse_args(['build','--revision','a'*40,'--destination','/tmp/test-build','--build-images'])
        with patch('evaluation.gke.commit_build.build_from_commit',return_value={'cloud_started':False}) as build:
            self.assertFalse(dispatch(args,ROOT)['cloud_started'])
            self.assertEqual(build.call_args.args[:3],(ROOT,'a'*40,Path('/tmp/test-build')))
            self.assertTrue(build.call_args.kwargs['build_images'])

class SerialLifecycleTests(unittest.TestCase):
    def test_history_and_verified_checkpoints_precede_second_cluster(self):
        import tempfile,time
        from types import SimpleNamespace
        from unittest.mock import patch
        from evaluation.gke import recoverable_run as recovery
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);events=[]
            protocol=models.read(ROOT/'protocols'/V2/'protocol.yaml')
            matrix=models.build_matrix(protocol,schedule=V2)
            ctx=SimpleNamespace(result=path,root=ROOT,run_id='fixture',state={},manifest={'schedule':V2,'matrix':matrix},
                config={'limits':{'cleanup_reserve_seconds':1800}},deadline=time.time()+43200,
                write=models.write,event=lambda *a,**k:None)
            class Driver:
                paths={};nodes=[]
                def start_repeat(self,repeat):
                    events.append(('cluster',repeat));ctx.state['resources']=[{'kind':'cluster','uid':str(repeat)}]
                def setup(self,spec):events.append(('setup',spec['repeat'],spec['cell']))
                def window(self,spec):
                    events.append(('window',spec['logical_id']))
                    target=path/spec['id'];target.mkdir();self.paths[spec['id']]=target
                    return {'path':str(target),'execution':{'state':'complete'},'evidence':{'valid':True},'numerical_agreement':{'state':'fail'}}
                def upload(self,spec,report):events.append(('export',spec['logical_id']));return {'verified':True,'uri':'gs://fixture/'+spec['id']}
                def thesis_history(self,spec):events.append(('history',));return {'export':{'verified':True},'continuation_safe':True}
                def finish(self):events.append(('finish',));return {'cluster_cleanup':{'residual':[]}}
            def diagnostic(context,directory,**kwargs):
                directory.mkdir(parents=True)
                return SimpleNamespace(start=lambda:None,stop=lambda:{'stopped':True})
            def checkpoint(*args):events.append(('checkpoint',));return {'verified':True}
            with patch('evaluation.gke.execution_diagnostics.ExecutionDiagnostics',side_effect=diagnostic), \
                 patch('evaluation.gke.minimal_readiness.check_cell',side_effect=lambda c,d,s,a:{'usable':True,'cell':s['cell']}), \
                 patch('evaluation.gke.provision.require_context'),patch('evaluation.gke.provision.assert_project_lease'), \
                 patch.object(recovery,'transition',return_value={'safe':True}),patch.object(recovery,'_checkpoint',side_effect=checkpoint), \
                 patch('evaluation.gke.thesis.claim_matrix',return_value={'claims':[{'verdict':'fail'}]}):
                report=recovery.execute(ctx,Driver())
            self.assertTrue(report['study_complete'],report['failure'])
            self.assertFalse(report['qualified']) # Numerical failure is a result, not a retry.
            self.assertEqual(report['attempts'],14)
            self.assertEqual([e[1] for e in events if e[0]=='window'],[r['id'] for r in matrix])
            history=events.index(('history',));second=events.index(('cluster',2))
            self.assertLess(events.index(('export',matrix[7]['id'])),history)
            self.assertLess(history,events.index(('window',matrix[8]['id'])))
            self.assertEqual(events[second-1],('checkpoint',))
            self.assertLess(events.index(('export',matrix[9]['id'])),second)
            self.assertEqual([e for e in events if e[0]=='cluster'],[('cluster',1),('cluster',2)])
