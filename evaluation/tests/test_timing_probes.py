import importlib.util
from pathlib import Path
import unittest
import types
import os
import json
import re
import sys
from unittest.mock import patch
from evaluation.workloads import common

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('timing_probe_contract', ROOT/'workloads/timing_probe_contract.py')

class TimingProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = importlib.util.module_from_spec(SPEC)
        SPEC.loader.exec_module(cls.contract)

    def test_offsets_and_recipes(self):
        c = self.contract
        plan = c.build_plan('astronomy', 'run-'+'x'*200)
        self.assertEqual(len(plan), 24)
        self.assertEqual([p['planned_offset_seconds'] for p in plan[::2]], [20,29,31,44,46,59,61,74,76,89,91,100])
        self.assertEqual([p['recipe'] for p in plan], ['checkout','expired']*12)
        self.assertEqual(sum(30 <= p['planned_offset_seconds'] < 90 for p in plan),16)
        ids = [s['request_id'] for p in plan for s in p['steps']]
        self.assertEqual(len(ids),len(set(ids)))
        self.assertTrue(all(len(i)<80 for i in ids))

    def test_exact_recipe_and_http_checkout(self):
        for demo in ('boutique','astronomy'):
            plan = self.contract.build_plan(demo, 'test')
            for p in plan:
                self.assertEqual(p['steps'][-1]['method'],'POST')
                self.assertEqual(p['steps'][-1]['phase'],'checkout')
                self.assertEqual([s['phase'] for s in p['steps'][:-1]], ['prerequisite']*2)
                body = str(p['steps'][-1]['body'])
                self.assertIn('2039' if p['recipe']=='checkout' else '2020',body)
                self.assertEqual(p['steps'][-1]['outcome'], {'boutique':{'checkout':'boutique_order','expired':'expired'},'astronomy':{'checkout':'astronomy_order','expired':'payment_failed'}}[demo][p['recipe']])

    def test_late_schedule_and_hard_deadline(self):
        c = self.contract
        self.assertEqual(c.wait_seconds(100,20,119),1)
        self.assertEqual(c.wait_seconds(100,20,122),0)
        self.assertEqual(c.request_timeout(100,219),1)
        self.assertEqual(c.request_timeout(100,220),0)
        self.assertEqual(c.request_timeout(100,101),10)

    def test_ledger_preserves_failure_and_actual_context(self):
        c=self.contract
        step=c.build_plan('boutique','abc')[1]['steps'][-1]
        rec=c.request_record(step,'abc','locust','2026-01-01T00:00:31Z','2026-01-01T00:00:32Z',31,1000,0,'timeout',False,{'trace_id':'abc','traceparent':'parent'})
        self.assertEqual(rec['status'],0)
        self.assertEqual(rec['exception'],'timeout')
        self.assertFalse(rec['completed'])
        self.assertEqual(rec['planned_offset_seconds'],20)
        self.assertEqual(rec['actual_offset_seconds'],31)
        self.assertEqual(rec['actual_traceparent'],'parent')
        self.assertEqual(rec.get('requested_trace_id'),'abc')
        self.assertEqual(rec.get('actual_trace_id'),'abc')
        self.assertEqual(rec['requested_traceparent'],'parent')
        self.assertEqual(rec['phase'],'checkout')

    def test_probe_sources_native_reporting_and_fixed_duration(self):
        js=(ROOT/'workloads/timing_probe.js').read_text()
        py=(ROOT/'workloads/timing_probe.py').read_text()
        self.assertIn('handleSummary',js)
        self.assertIn("from 'k6/http'",js)
        self.assertIn('currentTestRunDuration',js)
        self.assertIn('catch_response=True',py)
        self.assertIn('install_reports(events)',py)
        self.assertIn('time.monotonic()',py)
        self.assertIn('EVAL_PROBE_COMPLETE',js)
        self.assertIn('EVAL_PROBE_COMPLETE',py)

    def adapter(self):
        hook=types.SimpleNamespace(add_listener=lambda f:f)
        locust=types.SimpleNamespace(HttpUser=type('HttpUser',(),{}),task=lambda f:f,
            events=types.SimpleNamespace(request=hook,test_start=hook,test_stop=hook))
        stub={'locust':locust,'locust.exception':types.SimpleNamespace(StopUser=RuntimeError),
              'gevent':types.SimpleNamespace(),'common':common,
              'locust_reports':types.SimpleNamespace(install=lambda events:None),
              'timing_probe_contract':self.contract}
        spec=importlib.util.spec_from_file_location('isolated_timing_probe',ROOT/'workloads/timing_probe.py')
        adapter=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,stub),patch.dict('os.environ',{'RUN_ID':'unit','DEMO':'boutique'}):
            spec.loader.exec_module(adapter)
        adapter.ORIGIN=100
        return adapter

    def test_adapter_records_timeout_exception(self):
        adapter=self.adapter()
        user=adapter.TimingProbeUser()
        class Client:
            cookies=types.SimpleNamespace(clear=lambda:None)
            def request(self,*args,**kwargs): raise TimeoutError('socket timeout')
        user.client=Client()
        records=[]
        adapter.emit=lambda kind,row:records.append((kind,row))
        step=self.contract.build_plan('boutique','unit')[0]['steps'][-1]
        with patch.object(adapter.time,'monotonic',return_value=131):
            self.assertFalse(user.request_step(step))
        self.assertEqual([r[0] for r in records],['EVAL_ATTEMPT','EVAL_REQUEST'])
        terminal=records[-1][1]
        self.assertEqual(terminal['exception'],'socket timeout')
        self.assertEqual(terminal['actual_offset_seconds'],31)
        self.assertEqual(terminal['status'],0)
        self.assertFalse(terminal['native_reported'])

    def test_adapter_never_issues_request_at_deadline(self):
        adapter=self.adapter()
        user=adapter.TimingProbeUser()
        # No client is assigned: touching HTTP would fail with AttributeError.
        step=self.contract.build_plan('boutique','unit')[0]['steps'][-1]
        with patch.object(adapter.time,'monotonic',return_value=220):
            with self.assertRaisesRegex(RuntimeError,'deadline'):
                user.request_step(step)

    def test_adapter_native_catch_response_preserves_intentional_failure(self):
        adapter=self.adapter()
        user=adapter.TimingProbeUser()
        class Response:
            status_code=500
            text='expired; failed to complete the order'
            error=None
            def __enter__(self):return self
            def __exit__(self,*args):adapter.native_request_reported(context=self.context)
            def success(self):self.ok=True
            def failure(self,text):self.ok=False
        response=Response()
        class Client:
            cookies=types.SimpleNamespace(clear=lambda:None)
            def request(self,*args,**kwargs):
                self.kwargs=kwargs
                response.context=kwargs['context']
                return response
        user.client=Client()
        records=[]
        adapter.emit=lambda kind,row:records.append((kind,row))
        step=self.contract.build_plan('boutique','unit')[1]['steps'][-1]
        with patch.object(adapter.time,'monotonic',return_value=131):
            self.assertTrue(user.request_step(step))
        self.assertTrue(response.ok)
        self.assertTrue(user.client.kwargs['catch_response'])
        self.assertEqual(user.client.kwargs['timeout'],10)
        terminal=records[-1][1]
        self.assertEqual(terminal['status'],500)
        self.assertTrue(terminal['outcome_ok'])
        self.assertTrue(terminal['native_reported'])
        self.assertFalse(terminal['native_failure'])

    def test_window_declares_probe_protocol(self):
        marker=self.contract.window_record('r','k6','now')
        self.assertEqual(marker['offered_model'],'absolute-probe-schedule')
        self.assertEqual(marker['expected_probe_checkouts'],24)
        self.assertEqual(marker['intended_duration_seconds'],120)
        self.assertEqual(marker['probe_protocol_version'],1)

@unittest.skipUnless(os.environ.get('TIMING_PROBE_NATIVE_LOG_DIR'), 'native Docker fixture logs not requested')
class NativeTimingProbeTests(unittest.TestCase):
    def check_native(self,generator):
        log=(Path(os.environ['TIMING_PROBE_NATIVE_LOG_DIR'])/f'timing-probe-{generator}-native.log').read_text()
        def events(kind):
            return [json.loads(line.split(' ',1)[1]) for line in log.splitlines() if line.startswith(kind+' ')]
        records=events('EVAL_REQUEST')
        attempts=events('EVAL_ATTEMPT')
        complete=events('EVAL_PROBE_COMPLETE')
        self.assertEqual(len(complete),1,'native probe did not emit completion')
        self.assertTrue(complete[0]['complete'])
        self.assertTrue(119.75 <= complete[0]['actual_duration_seconds'] <= 120.25)
        self.assertEqual(len(records),72)
        self.assertEqual({r['request_id'] for r in records},{r['request_id'] for r in attempts})
        self.assertTrue(all(r['outcome_ok'] for r in records))
        checkouts=[r for r in records if r['phase']=='checkout']
        self.assertEqual(len(checkouts),24)
        self.assertEqual(sum(30 <= r['actual_offset_seconds'] < 90 for r in checkouts),16)
        self.assertTrue(all(abs(r['actual_offset_seconds']-r['planned_offset_seconds'])<.25 for r in checkouts))
        self.assertTrue(all(r['actual_offset_seconds']<20 for r in records if r['phase']=='prerequisite'))
        if generator=='k6':
            summary=json.loads(log.split('__EVAL_K6_SUMMARY_BEGIN__\n')[1].split('\n__EVAL_K6_SUMMARY_END__')[0])
            self.assertEqual(summary['metrics']['http_reqs']['values']['count'],72)
            self.assertEqual(summary['metrics']['iterations']['values']['count'],24)
            self.assertEqual(summary['metrics']['probe_scheduling_drops']['values']['count'],0)
        else:
            self.assertTrue(all(r['native_reported'] for r in records))
            self.assertFalse(any(r['native_failure'] for r in records))
            self.assertRegex(log,r'Aggregated\s+72\s+0\(0[.]00%\)')

    def test_k6_pinned_native_timing_and_reports(self):self.check_native('k6')
    def test_locust_pinned_native_timing_and_reports(self):self.check_native('locust')

if __name__=='__main__': unittest.main()
