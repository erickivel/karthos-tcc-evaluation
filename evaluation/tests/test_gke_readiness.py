import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from evaluation.gke import readiness
from evaluation.gke.models import read


class ReadinessTests(unittest.TestCase):
    def test_approved_three_hour_readiness_is_versioned_and_keeps_reserve(self):
        definition=readiness.definition()
        self.assertEqual(definition['version'],3)
        self.assertEqual(definition['seconds'],10800)
        self.assertEqual(definition['reserve_seconds'],1200)
        ctx=self.context(Path('.'))
        self.assertEqual(readiness.cutoff(ctx),10800)
        ctx.deadline=9000
        self.assertEqual(readiness.cutoff(ctx),9000)
        self.assertEqual(len(definition['matrix']),13)
        self.assertEqual(sum(row['duration_seconds'] for row in definition['matrix']),1020)

    def test_order_and_full_coverage(self):
        matrix = readiness.matrix()
        self.assertEqual(len(matrix), 13)
        self.assertEqual(sum(row['duration_seconds'] for row in matrix), 1020)
        self.assertEqual([row['cell'] for row in matrix if row['phase'] == 'smoke'],
                         ['astronomy-mesh', 'boutique-mesh', 'boutique-trace', 'astronomy-trace'])
        self.assertEqual([row['scenario'] for row in matrix if row['phase'] == 'resource'],
                         ['network', 'cpu-memory', 'storage'])
        self.assertEqual(len({row['id'] for row in matrix}), 13)

    def run_cases(self, root, failure=None, reset_error=None, receipt=True, cost=76, remaining=40000):
        self.calls = []
        ctx = SimpleNamespace(result=root, run_id='current', manifest={
            'matrix': [dict(duration_seconds=300)], 'cost_preview': {'gross_estimate': cost}},
            config={'limits': {'launch_ceiling_usd': 80, 'gross_limit_usd': 100, 'cleanup_reserve_seconds': 1800}},
            deadline=remaining, state={'started_at': 0}, write=lambda path, data: readiness.write(path, data))

        def check(row):
            self.calls.append(row['id'])
            return {'state': 'failed' if row['id'] == failure else 'passed', 'forecast_seconds': 1000, 'export': {'verified': True}}

        def reset():
            self.calls.append('reset')
            if reset_error:
                raise ValueError(reset_error)
            return {'valid': True, 'seconds': 10}

        def publish(path):
            self.calls.append('publish:' + path.name)
            return {'verified': receipt}

        return readiness.execute(ctx, check, reset, publish, lambda: 1)

    def test_all_passes_require_publish_and_reset(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            result = self.run_cases(root)
            self.assertTrue(result['ready'])
            self.assertEqual(self.calls[-2:], ['publish:matrix', 'reset'])
            self.assertEqual(len(read(root/'readiness/matrix/matrix.json')['cases']), 13)

    def test_local_failure_keeps_other_cases_and_prevents_reset(self):
        with tempfile.TemporaryDirectory() as folder:
            first = readiness.matrix()[0]['id']
            result = self.run_cases(Path(folder), failure=first)
            self.assertFalse(result['ready'])
            self.assertEqual(len(self.calls), 14)
            self.assertNotIn('reset', self.calls)

    def test_unverified_export_reset_cost_and_time_block_handoff(self):
        for kwargs in ({'receipt': False}, {'reset_error': 'foreign UID'}, {'cost': 81}, {'remaining': 900}):
            with self.subTest(kwargs=kwargs), tempfile.TemporaryDirectory() as folder:
                self.assertFalse(self.run_cases(Path(folder), **kwargs)['ready'])

    def test_unsafe_failure_stops_later_traffic_and_keeps_failed_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            ctx = self.context(root)
            calls = []
            def check(row):
                calls.append(row['id'])
                return {'state':'failed','reason':'capture lost','unsafe':True,
                        'export':{'verified':True,'uri':'gs://fixture/failed-receipt'}}
            result = readiness.execute(ctx, check, lambda:self.fail('unsafe reset'),
                                       lambda path:{'verified':True}, lambda:1)
            self.assertEqual(len(calls),1)
            self.assertEqual(result['cases'][0]['export']['uri'],'gs://fixture/failed-receipt')
            self.assertTrue(all(row['state']=='unattempted' for row in result['cases'][1:]))
            self.assertFalse(result['ready'])

    def context(self, root):
        return SimpleNamespace(result=root, run_id='current', manifest={
            'matrix':[dict(duration_seconds=300)], 'cost_preview':{'gross_estimate':76}},
            config={'limits':{'launch_ceiling_usd':80,'gross_limit_usd':100,'cleanup_reserve_seconds':1800}},
            deadline=43200, state={'started_at':0}, write=readiness.write)

    def test_infrastructure_watchdog_uses_shorter_deadline_until_handoff(self):
        from evaluation.gke.provision import _inventory_record
        ctx = self.context(Path('.'))
        ctx.config['gcp'] = {'project_id':'fixture'}
        ctx.manifest['readiness_check'] = readiness.definition()
        self.assertEqual(_inventory_record(ctx, [], [])['deadline'],10800)
        ctx.state['readiness_handoff_at'] = 1200
        self.assertEqual(_inventory_record(ctx, [], [])['deadline'],10800)
        self.assertNotIn('readiness_handoff', _inventory_record(ctx, [], []))

    def test_readiness_handoff_starts_original_matrix_and_rebuilds_only_for_repeat_two(self):
        import time
        from evaluation.gke import run, models
        from evaluation.tests.test_gke_run import FakeDriver, RunTests
        for schedule, count in ((models.REHEARSAL,16),(models.QUALIFICATION,46)):
            with self.subTest(schedule=schedule), tempfile.TemporaryDirectory() as folder:
                ctx = RunTests().context(folder)
                ctx.run_id = 'current'
                ctx.state = {'started_at':time.time()}
                ctx.manifest.update(schedule=schedule, readiness_check=readiness.definition(),
                                    cost_preview={'gross_estimate':76},
                                    matrix=models.build_matrix(ctx.manifest['protocol_definition'],schedule=schedule))
                ctx.config['limits'].update(launch_ceiling_usd=80,gross_limit_usd=100)
                driver = FakeDriver()
                def gate(context, active):
                    active.start_repeat(1)
                    return readiness.execute(context,
                        lambda row:{'state':'passed','forecast_seconds':600,'setup_seconds':60,
                                    'preflight_seconds':60,'export':{'verified':True}},
                        lambda:active.events.append(('reset',)) or {'valid':True},
                        lambda path:{'verified':True})
                result = run.execute_matrix(ctx,driver,gate=gate)
                self.assertEqual(result['executed_windows'],count)
                self.assertEqual(driver.events[:2],[('repeat',1),('reset',)])
                self.assertEqual([event[1] for event in driver.events if event[0]=='repeat'],
                                 [1] if count==16 else [1,2])
                self.assertTrue(result['readiness']['ready'])

    def test_failed_real_gate_never_starts_first_frozen_window(self):
        import time
        from evaluation.gke import run
        from evaluation.tests.test_gke_run import FakeDriver, RunTests
        for failed in ('cost','case','case-export','matrix-export','reset','deadline'):
            with self.subTest(failed=failed), tempfile.TemporaryDirectory() as folder:
                ctx = RunTests().context(folder)
                ctx.run_id = 'current'
                ctx.state = {'started_at':time.time()}
                ctx.manifest.update(readiness_check=readiness.definition(),
                                    cost_preview={'gross_estimate':81 if failed=='cost' else 76})
                ctx.config['limits'].update(launch_ceiling_usd=80,gross_limit_usd=100)
                driver = FakeDriver()
                def gate(context, active):
                    return readiness.execute(context,
                        lambda row:{'state':'failed' if failed=='case' else 'passed',
                                    'forecast_seconds':6000 if failed=='deadline' else 600,
                                    'setup_seconds':60,'preflight_seconds':60,
                                    'export':{'verified':failed!='case-export'}},
                        lambda:{'valid':failed!='reset'},
                        lambda path:{'verified':failed!='matrix-export'})
                result = run.execute_matrix(ctx,driver,gate=gate)
                self.assertEqual(result['executed_windows'],0)
                self.assertEqual(driver.events,[('finish',)])
                self.assertFalse(result['readiness']['ready'])

    def test_reset_rejects_uid_changed_between_inventory_and_delete(self):
        import json
        from evaluation.gke.run import _owned_delete
        calls = []
        def kube(*args, **kwargs):
            calls.append(args)
            return SimpleNamespace(stdout=json.dumps({'metadata':{
                'uid':'new','resourceVersion':'10','labels':{
                'evaluation.karthos.dev/owner':'gke-qualification-v1',
                'evaluation.karthos.dev/run-id':'current'}}}))
        with self.assertRaisesRegex(ValueError,'UID changed'):
            _owned_delete(SimpleNamespace(kube=kube),'Namespace','fixture',None,
                          'gke-qualification-v1','current',cluster=True,expected_uid='old')
        self.assertEqual(len(calls),1)

    def test_cpu_and_memory_probe_matches_sample_to_exact_pod_uid_and_node(self):
        import gzip
        import json
        from evaluation.gke.readiness_live import _cpu_memory_samples
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'kubelet-node-a.ndjson.gz'
            def row(node, uid):
                return json.dumps({'node_name':node,'received_at':'time',
                    'summary':{'pods':[{'podRef':{'uid':uid},'containers':[{
                    'name':'stimulus','cpu':{'time':'cpu-time','usageCoreNanoSeconds':1000},
                    'memory':{'time':'memory-time','workingSetBytes':2000}}]}]}}).encode()+b'\n'
            raw = gzip.compress(row('node-b','uid-a')+row('node-a','other-uid')+row('node-a','uid-a'))
            path.write_bytes(raw + b'not-a-complete-gzip-record')
            capture = SimpleNamespace(sample_writers={'sample':SimpleNamespace(path=path,records=3,stored_bytes=len(raw))})
            result = _cpu_memory_samples(capture,[{'uid':'uid-a','node_name':'node-a'},{'uid':'uid-b','node_name':'node-b'}])
            self.assertEqual(set(result),{'uid-a'})
            self.assertEqual(result['uid-a']['memory']['workingSetBytes'],2000)

    def test_archive_receipt_failure_keeps_current_launch_out_of_measured_phase(self):
        with tempfile.TemporaryDirectory() as folder:
            ctx = self.context(Path(folder))
            def failed_commit(summary):
                raise IOError('handoff archive readback mismatch')
            result = readiness.execute(ctx,
                lambda row:{'state':'passed','forecast_seconds':1000,'export':{'verified':True}},
                lambda:{'valid':True}, lambda path:{'verified':True}, lambda:1,
                commit=failed_commit)
            self.assertEqual(result['counts']['passed'],13)
            self.assertFalse(result['ready'])
            self.assertIn('handoff archive readback',result['error'])

    def test_launcher_rejects_legacy_probe_gate_before_work(self):
        from evaluation.gke.models import THESIS, build_matrix
        ctx=self.context(Path('.'))
        protocol=read(Path(__file__).resolve().parents[2]/'protocols'/THESIS/'protocol.yaml')
        ctx.manifest.update(schedule=THESIS,protocol_definition=protocol,matrix=build_matrix(protocol,schedule=THESIS))
        for legacy in (None,readiness.definition()):
            ctx.manifest['readiness_check']=legacy
            with self.assertRaisesRegex(ValueError,'minimal readiness|legacy readiness'):
                readiness.validate_launch(ctx)

    def test_readiness_preflight_failure_marks_all_cases_unattempted(self):
        with tempfile.TemporaryDirectory() as folder:
            ctx = self.context(Path(folder))
            result = readiness.execute(ctx,lambda _:self.fail('unexpected probe'),
                                       None,lambda path:{'verified':True},lambda:1,
                                       initial_failure='controller bundle validation failed')
            self.assertEqual(result['counts']['unattempted'],13)
            self.assertFalse(result['ready'])
            self.assertIn('controller bundle',result['error'])
