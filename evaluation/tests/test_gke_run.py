"""Injected orchestration fixtures; never execute cloud or Kubernetes commands."""
import importlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from evaluation.gke.models import build_matrix,write

ROOT=Path(__file__).resolve().parents[2]


class FakeDriver:
    def __init__(self,fail=None,numeric='pass'):
        self.events=[];self.fail=fail;self.numeric=numeric
    def start_repeat(self,repeat):self.events.append(('repeat',repeat))
    def setup(self,spec):self.events.append(('setup',spec['repeat'],spec['cell']))
    def window(self,spec):
        self.events.append(('window',spec['id']))
        if spec['id']==self.fail:raise RuntimeError('required evidence unavailable')
        return {'execution':{'state':'complete'},'evidence':{'valid':True},'numerical_agreement':{'state':self.numeric},'state':'diagnostic'}
    def upload(self,spec,report):self.events.append(('upload',spec['id']));return {'verified':True,'uri':'gs://fixture/'+spec['id']}
    def finish(self):self.events.append(('finish',));return {'cluster_deleted':True,'controller_retained':True}


class RunTests(unittest.TestCase):
    def module(self):
        try:return importlib.import_module('evaluation.gke.run')
        except ImportError:self.fail('46-window orchestration is missing')
    def context(self,directory):
        protocol=json.loads((ROOT/'protocols/gke-qualification-v1/protocol.yaml').read_text())
        return SimpleNamespace(root=ROOT,result=Path(directory),manifest={'protocol_definition':protocol,'matrix':build_matrix(protocol)},write=write,
                               event=lambda *a,**k:None,deadline=time.time()+43200,config={'limits':{'cleanup_reserve_seconds':1800}})
    def test_all_46_windows_execute_serially_and_rebuild_between_repetitions(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as d:
            ctx=self.context(d);driver=FakeDriver();report=m.execute_matrix(ctx,driver)
            self.assertEqual([x[1] for x in driver.events if x[0]=='window'],[x['id'] for x in ctx.manifest['matrix']])
            self.assertEqual([x for x in driver.events if x[0]=='repeat'],[('repeat',1),('repeat',2)])
            self.assertEqual(len([x for x in driver.events if x[0]=='upload']),46)
            self.assertEqual(driver.events[-1],('finish',));self.assertEqual(report['executed_windows'],46)
    def test_bounded_verification_finishes_at_a_verified_window_boundary(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as d:
            ctx=self.context(d)
            ctx.manifest['verification_observation']={'mode':'bounded execution verification','seconds':1800}
            driver=FakeDriver()
            original=driver.window
            ends=iter(('2026-11-05T10:01:00Z','2026-11-05T10:20:00Z','2026-11-05T10:31:00Z'))
            def window(spec):
                return dict(original(spec),manifest={'start':'2026-11-05T10:00:00Z','end':next(ends)})
            driver.window=window
            report=m.execute_matrix(ctx,driver)
            self.assertEqual(report['executed_windows'],3)
            self.assertEqual(report['execution_verification']['state'],'complete')
            self.assertEqual(report['execution_verification']['observed_elapsed_seconds'],1860)
            self.assertFalse(report['qualified'])
            self.assertEqual(len(json.loads((ctx.result/'matrix.json').read_text())),46)
            self.assertEqual(driver.events[-1],('finish',))

    def test_numeric_disagreement_is_retained_without_aborting_execution(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as d:
            report=m.execute_matrix(self.context(d),FakeDriver(numeric='fail'))
            self.assertEqual(report['executed_windows'],46)
            self.assertTrue(all(x['numerical_agreement']=='fail' for x in report['windows']))
    def test_failure_exports_partial_window_stops_baselines_and_always_cleans_cluster(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as d:
            ctx=self.context(d);first=ctx.manifest['matrix'][0]['id'];driver=FakeDriver(fail=first)
            report=m.execute_matrix(ctx,driver)
            self.assertEqual(len([x for x in driver.events if x[0]=='window']),1)
            self.assertIn(('upload',first),driver.events);self.assertEqual(driver.events[-1],('finish',))
            self.assertEqual(len(json.loads((Path(d)/'matrix.json').read_text())),46)
            self.assertEqual(report['state'],'incomplete')
    def test_unhandled_window_exception_keeps_auditable_traceback(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as d:
            ctx=self.context(d);first=ctx.manifest['matrix'][0]['id']
            report=m.execute_matrix(ctx,FakeDriver(fail=first))
            detail=json.loads((Path(d)/'failure-traces'/(first+'.json')).read_text())
            self.assertEqual(detail['error_type'],'RuntimeError')
            self.assertEqual(detail['message'],'required evidence unavailable')
            self.assertIn('driver.window(spec)',detail['traceback'])
            self.assertEqual(report['executed_windows'],1)
            self.assertFalse(report['windows'][0]['evidence_valid'])

    def test_unverified_upload_blocks_further_work(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as d:
            driver=FakeDriver();driver.upload=lambda *a:{'verified':False}
            report=m.execute_matrix(self.context(d),driver)
            self.assertEqual(report['executed_windows'],1);self.assertEqual(report['state'],'incomplete')
    def test_expected_restart_gap_is_retained_as_incomplete_not_pass(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as d:
            driver=FakeDriver();original=driver.window
            def window(spec):
                report=original(spec)
                if spec['phase']=='restart':report['evidence']['valid']=False;report['traffic']={'expected_disruption':True}
                return report
            driver.window=window;report=m.execute_matrix(self.context(d),driver)
            self.assertEqual(report['executed_windows'],46)
            restarts=[x for x in report['windows'] if x['phase']=='restart']
            self.assertTrue(all(x['state']=='diagnostic-incomplete' and not x['evidence_valid'] for x in restarts))
    def test_matrix_omission_rejected_before_cloud_driver(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as d:
            ctx=self.context(d);ctx.manifest['matrix'].pop();driver=FakeDriver()
            with self.assertRaises(ValueError):m.execute_matrix(ctx,driver)
            self.assertEqual(driver.events,[])
    def test_clock_gate_checks_every_agent_and_explicit_generator_node(self):
        m=self.module();pods={'agent:'+n:{'spec':{'nodeName':n}} for n in ('n1','n2','n3')}
        def check(capture,node_name,*,persistent=False):
            self.assertTrue(persistent,'GKE must exclude exec stream startup from sample bounds')
            return {'valid':True,'clock_uncertainty_seconds':{'n1':.01,'n2':.02,'n3':.03}[node_name]}
        with patch('evaluation.timing_capture.clock_samples',side_effect=check) as sample:
            result=m.capture_clocks(SimpleNamespace(pods=pods),'n2')
        self.assertEqual(sample.call_count,3);self.assertEqual(result['clock_uncertainty_seconds'],.03);self.assertTrue(result['valid'])
        with self.assertRaises(ValueError):m.capture_clocks(SimpleNamespace(pods=pods),'other')

    def test_cleanup_residuals_prevent_complete_execution_claim(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as d:
            driver=FakeDriver();driver.finish=lambda:{'cluster_cleanup':{'residual':['cluster']}}
            report=m.execute_matrix(self.context(d),driver)
            self.assertEqual(report['state'],'incomplete')

    def test_diagnostic_worker_requires_observed_generator_marker(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as d:
            ctx=self.context(d);ctx.kube=lambda *a,**k:SimpleNamespace(returncode=0,stdout='EVAL_WINDOW {"started_at":"1970-01-01T00:00:01Z"}\n')
            ctx.json=lambda *a:{'items':[{'metadata':{'name':'generator','uid':'pod-uid'},'spec':{'nodeName':'infra'},'status':{'phase':'Running'}}]}
            driver=m.LiveDriver(ctx);driver.cell=SimpleNamespace(namespace='app');driver.nodes=[{'slot':'infra','name':'infra'}]
            path=Path(d)/'window';write(path/'manifest.json',{'phase':'generator_and_export'});write(path/'job.json',{'metadata':{'name':'generator'},'spec':{'template':{'spec':{'containers':[{'name':'generator'}]}}}})
            marker=driver._wait_measurement(path,__import__('threading').Event())
            self.assertEqual(marker['started_at'],'1970-01-01T00:00:01Z')
            self.assertTrue((path/'diagnostic-generator-start.json').exists())

    def test_each_frozen_window_generator_job_name_fits_kubernetes(self):
        self.module()
        from evaluation import timing_pilot as t
        from evaluation.protocol import Cell
        with tempfile.TemporaryDirectory() as d:
            for spec in self.context(d).manifest['matrix']:
                with patch.dict(t.r.CONFIG,{'k6_image':'k6@sha256:x','boutique_locust_image':'locust@sha256:x','astronomy_locust_image':'locust@sha256:x','artifact_recovery_seconds':1800}):
                    job=t.make_job(Cell(spec['demo'],spec['source'],spec['repeat']),dict(spec,name=spec['id'],kind='confirmation'),'unique-window')
                self.assertLessEqual(len(job['metadata']['name']),63,spec['id'])

    def test_live_driver_packages_sealed_window_before_unique_verified_upload(self):
        m=self.module()
        import tarfile
        with tempfile.TemporaryDirectory() as d:
            ctx=self.context(d);ctx.run_id='fixture';ctx.state={'terraform':{'foundation':{'evidence_bucket':'fixture-bucket'}}}
            spec=ctx.manifest['matrix'][0];path=Path(d)/'window';write(path/'raw.txt','source bytes')
            driver=m.LiveDriver(ctx);driver.paths[spec['id']]=path
            def upload(ctx,folder,**kw):
                archives=list(Path(folder).glob('*.tar.gz'))
                self.assertEqual(len(archives),1)
                with tarfile.open(archives[0]) as tar:
                    self.assertEqual(tar.extractfile('raw.txt').read(),b'source bytes')
                    self.assertIn('GKE-SHA256SUMS',tar.getnames())
                self.assertTrue(kw['prefix'].endswith('/'+spec['id']))
                return {'verified':True,'uri':'gs://fixture/unique'}
            with patch.object(m.exports,'upload',side_effect=upload):receipt=driver.upload(spec,{})
            self.assertTrue(receipt['verified'])

    def test_owned_cleanup_rejects_replaced_foreign_resources_before_delete(self):
        m=self.module();calls=[]
        def kube(*args,**kwargs):
            calls.append(args);return SimpleNamespace(stdout=json.dumps({'metadata':{'uid':'new','resourceVersion':'7','labels':{'evaluation.karthos.dev/owner':'foreign'}}}))
        with self.assertRaises(ValueError):m._owned_delete(SimpleNamespace(kube=kube),'Pod','helper','app','gke-qualification-v1','run')
        self.assertEqual(len(calls),1)

    def test_live_driver_normal_window_connects_timing_reference_consumer_selection_and_evaluator(self):
        m=self.module()
        from evaluation.protocol import Cell
        from evaluation import timing_pilot
        from evaluation.gke import evaluate as evaluator,consumer
        with tempfile.TemporaryDirectory() as d:
            ctx=self.context(d);ctx.run_id='fixture';ctx.config['readiness']={'resource_helper_image':'helper@sha256:'+'a'*64}
            spec=ctx.manifest['matrix'][0];driver=m.LiveDriver(ctx);driver.cell=Cell(spec['demo'],spec['source'],spec['repeat'])
            driver.setup_path=Path(d)/'setup';driver.setup_path.mkdir();write(driver.setup_path/'resolved-plan.json',{'source':'traces.servicegraph'})
            def timing(cell,parent,window,study,*,measure):
                self.assertIn(spec['id'],study)
                path=parent/'windows'/window['name'];path.mkdir(parents=True)
                write(path/'manifest.json',{'start':1,'end':2,'generator_artifacts_complete':True})
                return {'independent_valid':True}
            def reference_stop(ns,token,path,pods):write(path/'reference-status.json',{'valid':True});return {'valid':True}
            with patch('evaluation.gke.setup.verify_versions',return_value={'valid':True}) as version_check,patch.object(driver,'_reference_start',return_value=({'kind':'DaemonSet','metadata':{'name':'reference','namespace':driver.cell.namespace}},{})),patch.object(driver,'_reference_stop',side_effect=reference_stop),patch.object(driver,'_selection',return_value={'state':'pass'}),patch.object(timing_pilot,'run_window',side_effect=timing) as run_window,patch.object(evaluator,'capture_extended'),patch.object(evaluator,'evaluate',return_value={'execution':{'state':'complete'},'evidence':{'valid':True}}),patch.object(consumer,'capture_consumer',return_value={'state':'observed'}),patch.object(m,'_owned_delete'):
                result=driver.window(spec)
            self.assertTrue(result['evidence']['valid']);self.assertEqual(run_window.call_count,1);self.assertEqual(version_check.call_count,2)
            self.assertTrue((Path(result['path'])/'diagnostic-cleanup.json').exists())
            self.assertEqual(json.loads((Path(result['path'])/'setup-resolved-plan.json').read_text()),{'source':'traces.servicegraph'})

    def test_capture_setup_failure_keeps_original_reason_and_nonempty_setup_evidence(self):
        m=self.module()
        from evaluation.protocol import Cell
        from evaluation import timing_pilot
        from evaluation.gke import evaluate as evaluator
        with tempfile.TemporaryDirectory() as d:
            ctx=self.context(d);ctx.run_id='fixture';spec=ctx.manifest['matrix'][0]
            driver=m.LiveDriver(ctx);driver.cell=Cell(spec['demo'],spec['source'],spec['repeat'])
            driver.setup_path=Path(d)/'setup';driver.setup_path.mkdir();write(driver.setup_path/'resolved-plan.json',{'source':'traces.servicegraph'})
            def broken_capture(cell,parent,window,study,*,measure):
                path=parent/'windows'/window['name'];path.mkdir(parents=True)
                write(path/'manifest.json',{'phase':'incomplete','error':'original capture setup error'})
                raise RuntimeError('original capture setup error')
            def stop(ns,token,path,pods):write(path/'reference-status.json',{'valid':False});return {'valid':False}
            with patch('evaluation.gke.setup.verify_versions',return_value={'valid':True}),patch.object(driver,'_reference_start',return_value=({'kind':'DaemonSet','metadata':{'name':'ref','namespace':driver.cell.namespace}},{})),patch.object(driver,'_reference_stop',side_effect=stop),patch.object(timing_pilot,'run_window',side_effect=broken_capture),patch.object(evaluator,'evaluate',return_value={'execution':{'state':'incomplete'},'evidence':{'valid':False}}),patch.object(m,'_owned_delete'):
                result=driver.window(spec)
            self.assertEqual(result['reason'],'original capture setup error')
            detail=json.loads((Path(result['path'])/'timing-failure.json').read_text())
            self.assertEqual(detail['error_type'],'RuntimeError')
            self.assertIn('broken_capture',detail['traceback'])
            self.assertFalse(result['evidence']['valid'])
            self.assertEqual(json.loads((Path(result['path'])/'setup-resolved-plan.json').read_text()),{'source':'traces.servicegraph'})

    def test_reference_acquisition_gate_rejects_lost_start_gap_duplicate_and_clock_regression(self):
        m=self.module()
        def row(at):return {'node':'n','node_uid':'nu','observer_pod_uid':'pu','run_id':'w','source':'linux-proc','metric':'memory.MemFree','dimensions':{},'value':1,'unit':'kB','boot_id':'b','acquisition_started_at':at,'acquisition_ended_at':at+.01}
        valid=[row(at) for at in range(11)]
        self.assertTrue(m.validate_reference_rows(valid,'n','nu','pu','w',1,9)['valid'])
        for bad in (valid[4:],valid[:4]+valid[8:],valid+[valid[-1]],valid[:5]+[row(2)]+valid[5:]):
            self.assertFalse(m.validate_reference_rows(bad,'n','nu','pu','w',1,9)['valid'])

    def test_gke_cell_reset_requires_exact_owner_run_and_namespace_uid(self):
        import time
        from evaluation import runner
        from evaluation.gke import setup
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            run_id = 'fixture-gke'
            context = SimpleNamespace(root=root, result=root, work=root, kubeconfig=root/'kubeconfig',
                deadline=time.time()+300, run_id=run_id,
                manifest={'product_snapshot': str(root), 'image_lock': {}, 'baseline_config': {},
                          'product_pins': {'karthos_revision': 'fixture'}},
                config={'infrastructure': {}, 'limits': {'cleanup_reserve_seconds': 10}})
            deleted = []
            owner = {'evaluation.karthos.dev/owner': 'gke-qualification-v1',
                     'evaluation.karthos.dev/run-id': run_id}
            def kube(*args, **kwargs):
                if len(args)>=3 and args[0]=='get' and args[1].lower()=='namespace' and args[2]=='karthos-eval-boutique':
                    body = {'metadata': {'name': args[2], 'labels': owner, 'uid': 'current-uid', 'resourceVersion': '7'}}
                    return SimpleNamespace(returncode=0,stdout=json.dumps(body))
                if len(args)>=2 and args[0]=='get' and args[1].lower()=='namespace':
                    return SimpleNamespace(returncode=0,stdout='')
                if args[0] == 'delete':
                    deleted.append((args,json.loads(kwargs['input_text'])))
                return SimpleNamespace(returncode=0,stdout='')
            context.kube=kube
            context.json=lambda *args:{'items':[]}
            with (patch.object(runner,'release_exists',return_value=False),
                  patch.object(runner,'ownership',return_value=True),
                  patch.dict('os.environ',{'GKE_EVAL_PRODUCT_ROOT': str(root)}),
                  setup.legacy_context(context)):
                runner.reset_cell(False)
                self.assertEqual(len(deleted),1)
                args, body = deleted[0]
                self.assertEqual(args[:3],('delete','--raw','/api/v1/namespaces/karthos-eval-boutique'))
                self.assertEqual(body['preconditions'],{'uid':'current-uid','resourceVersion':'7'})
                owner['evaluation.karthos.dev/run-id']='different-run'
                with self.assertRaisesRegex(ValueError,'ownership changed'):
                    runner.reset_cell(False)
                self.assertEqual(len(deleted),1)

    def test_final_cleanup_failure_retains_preparation_evidence_without_claiming_completion(self):
        m=self.module()
        import contextlib
        from evaluation.gke import setup,provision
        with tempfile.TemporaryDirectory() as d:
            ctx=self.context(d);ctx.run_id='fixture';ctx.state={'terraform':{'foundation':{'evidence_bucket':'fixture-bucket'}}}
            def execute(ctx,driver,*,gate):
                for name in ('matrix.json','matrix.csv','matrix.html','run-report.json'):write(ctx.result/name,{} if name.endswith('.json') else 'fixture')
                return {'state':'complete','executed_windows':46}
            write(ctx.result/'infrastructure/helm-renderer-demo.stderr.log','renderer failure evidence')
            write(ctx.result/'failure-traces/window.json',{'message':'original failure','traceback':'failing code boundary'})
            with patch('evaluation.gke.readiness.validate_launch'),patch.object(setup,'stage'),patch.object(setup,'legacy_context',side_effect=lambda *a:contextlib.nullcontext()),patch.object(m,'execute_matrix',side_effect=execute),patch.object(m.exports,'upload',return_value={'verified':True,'uri':'gs://fixture-bucket/gke-qualification-v1/runs/fixture/final-summary/'}),patch.object(provision,'request_final_cleanup',side_effect=ValueError('pointer update unverified')):
                report=m.run(ctx)
            self.assertEqual(report['state'],'incomplete')
            self.assertFalse(report['final_cleanup_request']['completion_confirmed'])
            self.assertEqual(report['final_cleanup_request']['state'],'cleanup-request-unverified')
            self.assertEqual((ctx.result/'final-summary/infrastructure/helm-renderer-demo.stderr.log').read_text(),'renderer failure evidence')
            self.assertEqual(json.loads((ctx.result/'final-summary/failure-traces/window.json').read_text())['message'],'original failure')

    def test_network_stimulus_waits_for_ready_server_before_starting_client(self):
        m=self.module()
        import threading
        from evaluation.gke import provision
        from evaluation.gke.diagnostics.resources import build_resources_both
        with tempfile.TemporaryDirectory() as d:
            ctx=self.context(d);ctx.run_id='fixture';ctx.config['readiness']={'resource_helper_image':'helper@sha256:'+'a'*64}
            nodes=[{'slot':slot,'name':slot,'uid':'uid-'+slot} for slot in ('infra','app-a','app-b')]
            spec=next(row for row in ctx.manifest['matrix'] if row['phase']=='resource' and row['scenario']=='network');token='window-token';events=[]
            objects=build_resources_both('network','app',nodes,ctx.config['readiness']['resource_helper_image'],token,300)
            server, client = objects
            ready_server = dict(server,
                metadata=dict(server['metadata'], uid='server-uid'),
                spec=dict(server['spec'], nodeName='app-b'),
                status={'phase':'Running','podIP':'10.20.30.40',
                        'conditions':[{'type':'Ready','status':'True'}]})
            pods = [dict(ready_server, status={'phase':'Succeeded'}),
                    dict(client, status={'phase':'Succeeded'})]
            ctx.json = lambda *a: ready_server if a[:2] == ('get','pod') else {'items':pods}
            def kube(*args,**kwargs):
                if args[0]=='wait':events.append('ready')
                return SimpleNamespace(stdout='{"end":{"sum_received":{"bytes":1310720}}}',returncode=0)
            ctx.kube=kube
            driver=m.LiveDriver(ctx);driver.nodes=nodes;driver.cell=SimpleNamespace(namespace='app')
            def apply(items):
                obj, = items
                role = obj['metadata']['name'].rsplit('-', 1)[-1]
                events.append(role)
                if role == 'client':
                    self.assertEqual(obj['spec']['containers'][0]['args'][:2], ['--client','10.20.30.40'])
            state={}
            with patch.object(driver,'_wait_measurement',return_value={'started_at':'window-start'}),patch.object(driver,'_apply_objects',side_effect=apply),patch.object(provision,'arm_watchdog') as arm:
                driver._stimulus(spec,Path(d),token,threading.Event(),state,[])
            self.assertEqual(events,['server','ready','client']);self.assertEqual(state['state'],'complete')
            self.assertEqual(json.loads((Path(d)/'stimulus-network-target.json').read_text())['pod_uid'], 'server-uid')
            arm.assert_not_called()

    def test_interrupt_still_exports_attempt_and_writes_partial_matrix_before_cleanup(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as d:
            driver=FakeDriver()
            def interrupted(spec):raise KeyboardInterrupt('controller interrupted')
            driver.window=interrupted
            report=m.execute_matrix(self.context(d),driver)
            self.assertEqual(report['state'],'incomplete');self.assertEqual(report['executed_windows'],1)
            self.assertTrue(any(event[0]=='upload' for event in driver.events));self.assertEqual(driver.events[-1],('finish',))

    def test_missing_required_evidence_enters_bounded_idle_hold_after_partial_export(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as d:
            ctx=self.context(d);driver=FakeDriver(fail=ctx.manifest['matrix'][0]['id'])
            def hold(spec,report):driver.events.append(('hold',spec['id']));return {'new_traffic_permitted':False}
            driver.failure_hold=hold
            report=m.execute_matrix(ctx,driver)
            sequence=[event[0] for event in driver.events]
            self.assertLess(sequence.index('upload'),sequence.index('hold'));self.assertLess(sequence.index('hold'),sequence.index('finish'))
            self.assertEqual(report['state'],'incomplete')

    def test_gcs_window_prefixes_follow_phase_tree_and_final_repetition_restarts(self):
        m=self.module()
        self.assertEqual(m.window_evidence_prefix({'repeat':1,'phase':'baseline','id':'baseline-window'}),'repeat-1/baseline/baseline-window')
        self.assertEqual(m.window_evidence_prefix({'repeat':2,'phase':'restart','id':'restart-gateway'}),'repeat-2/restarts/restart-gateway')
        self.assertEqual(m.window_evidence_prefix({'repeat':1,'phase':'resource','id':'resource-cpu'}),'repeat-1/diagnostics/resource/resource-cpu')
        with self.assertRaises(ValueError):m.window_evidence_prefix({'repeat':1,'phase':'restart','id':'restart'})
