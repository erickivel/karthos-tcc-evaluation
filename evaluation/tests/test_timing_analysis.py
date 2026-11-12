import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from evaluation.timing_analysis import (analyze_window, populations, quiet_delta,
                                        availability_intervals, weighted_duration)


class TimingAnalysisTests(unittest.TestCase):
    def test_boundary_crossing_preserves_two_populations(self):
        events=[dict(identity='a',start=59.9,completion=60.2,duration_ms=300,outcome_failed=False)]
        report=populations(events,0,60)
        self.assertEqual(report['event_start']['requests'],1)
        self.assertEqual(report['completion']['requests'],0)
        self.assertEqual(report['crosses_end'],['a'])

    def test_half_open_and_duration_uses_selected_events(self):
        events=[dict(identity=str(i),start=t,completion=t,duration_ms=d,outcome_failed=None)
                for i,(t,d) in enumerate([(0,10),(59,30),(60,1000)])]
        report=populations(events,0,60)
        self.assertEqual(report['completion']['requests'],2)
        self.assertEqual(report['completion']['duration_ms'],20)
        self.assertEqual(report['completion']['outcome_unknown'],2)

    def test_weighted_partition_mean(self):
        self.assertEqual(weighted_duration([{'mean_ms':10,'count':9},{'mean_ms':100,'count':1}])['value_ms'],19)
        self.assertIsNone(weighted_duration([{'mean_ms':10}])['value_ms'])

    @staticmethod
    def quiet(value, extra=False):
        keys={'{"__name__":"servicegraph_request_count","source":"trace"}':value}
        if extra: keys['{"__name__":"servicegraph_request_count","source":"trace","failed":"true"}']=1
        return dict(stable=True,records=[dict(observed_at=t,values=keys,sample_times={k:t for k in keys}) for t in (0,15,30)],measured_collection_interval_seconds=15)

    def test_quiet_exact_reset_and_missing_partition(self):
        self.assertEqual(quiet_delta(self.quiet(1),self.quiet(5),'trace')['requests'],4)
        self.assertIsNone(quiet_delta(self.quiet(5),self.quiet(1),'trace')['requests'])
        self.assertIn('reset',str(quiet_delta(self.quiet(5),self.quiet(1),'trace')))
        self.assertIsNone(quiet_delta(self.quiet(1),self.quiet(5,True),'trace')['requests'])
        bad=self.quiet(1); bad['records'][-1]['values']={}
        self.assertIsNone(quiet_delta(bad,self.quiet(5),'trace')['requests'])

    def test_source_partition_is_not_silently_accepted(self):
        self.assertIsNone(quiet_delta(self.quiet(1),self.quiet(5),'mesh')['requests'])

    def test_first_availability_is_interval_not_ingestion(self):
        rows=[dict(source='api',requested_at_ns=1_000_000_000,received_at_ns=2_000_000_000,response={'samples':[]}),
              dict(source='api',requested_at_ns=3_000_000_000,received_at_ns=4_000_000_000,response={'samples':[{'entity_id':'x','timestamp':0,'value':1}]})]
        result=availability_intervals(rows)
        self.assertEqual(result['observations'][0]['possible_first_availability_seconds'],[1,4])
        self.assertFalse(result['exact_ingestion_time_supported'])

    def test_negative_from_different_evaluation_point_is_not_a_bound(self):
        rows=[dict(source='api', query_id='api:count', params={'from':'A','to':'A'},
                   requested_at_ns=1_000_000_000,received_at_ns=2_000_000_000,response={'samples':[]}),
              dict(source='api', query_id='api:count', params={'from':'B','to':'B'},
                   requested_at_ns=3_000_000_000,received_at_ns=4_000_000_000,
                   response={'samples':[{'timestamp':'B','value':1}]})]
        result=availability_intervals(rows)
        self.assertEqual(result['observations'][0]['possible_first_availability_seconds'], [None,4])
        rows[0]['params']={'from':'B','to':'B'}
        result=availability_intervals(rows)
        self.assertEqual(result['observations'][0]['possible_first_availability_seconds'], [1,4])

    def test_clock_required_and_unsupported_claims(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            (path/'manifest.json').write_text(json.dumps(dict(start=0,end=120,source_id='trace')))
            report=analyze_window(path)
            self.assertEqual(report['schema_version'],'timing-verifier1.0.1')
            self.assertEqual(report['clock']['state'],'incomplete')
            self.assertFalse(report['clock']['supports_1s_boundary_claims'])
            self.assertEqual(report['windows']['inner']['bounds_seconds'],[30,90])
            self.assertEqual(report['unsupported_measurements']['exact_ingestion_time'],'unsupported')
            (path/'manifest.json').write_text(json.dumps(dict(start=0,end=120,clock_uncertainty_seconds=.25)))
            self.assertFalse(analyze_window(path)['clock']['supports_1s_boundary_claims'])
            (path/'manifest.json').write_text(json.dumps(dict(start=0,end=120,clock_uncertainty_seconds=.249)))
            self.assertTrue(analyze_window(path)['clock']['supports_1s_boundary_claims'])

    def test_observer_status_separate_boundary_and_inferred_start(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory); (path/'logs').mkdir()
            (path/'manifest.json').write_text(json.dumps(dict(start=0,end=120,run_id='r',clock_uncertainty_seconds=.01)))
            rows=[dict(observed_at=60.2,boundary='frontend',request_time_seconds='.3',status=503,run_id='r'),
                  dict(observed_at=60.2,boundary='checkout',request_time_seconds='.3',status=200,grpc_status='',run_id='')]
            (path/'logs/pod__eval-observer.log').write_text('\n'.join(json.dumps(x) for x in rows))
            report=analyze_window(path)
            checkout=report['windows']['whole']['observers']['checkout']['completion']
            self.assertEqual(checkout['outcome_unknown'],1)
            self.assertEqual(report['windows']['whole']['observers']['frontend']['completion']['outcome_errors'],1)
            self.assertTrue(report['events']['observers'][0]['start_inferred'])

    def test_full_trace_parent_identity_and_connector_error_distinct(self):
        from evaluation.timing_analysis import trace_events
        spans=[dict(trace_id='a',span_id='client',kind='client',service='frontend',start=59.8,end=60.3,failed=True),
               dict(trace_id='b',span_id='client',kind='client',service='frontend',start=59.8,end=60.3,failed=False),
               dict(trace_id='b',span_id='server',parent_id='client',kind='server',service='checkout',name='/PlaceOrder',start=59.9,end=60.2,failed=False,attributes={'rpc.system':'grpc','rpc.grpc.status_code':0})]
        with patch('evaluation.timing_analysis.raw_spans',return_value=spans):
            events,conflicts=trace_events(Path('/missing'))
        self.assertFalse(events[0]['connector_failed'])
        self.assertFalse(events[0]['outcome_failed'])
        self.assertTrue(events[0]['paired_frontend'])
        self.assertEqual(events[0]['start'],59.9)
        self.assertEqual(conflicts,[])

    def test_inner_parity_cannot_reuse_historical_whole(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            (path/'manifest.json').write_text(json.dumps(dict(start=0,end=120)))
            (path/'verification.json').write_text(json.dumps({'counts_path':{'exact_request_count_api':{'state':'pass'}}}))
            report=analyze_window(path)
            self.assertEqual(report['windows']['whole']['api_storage_parity']['state'],'retained')
            self.assertEqual(report['windows']['inner']['api_storage_parity']['state'],'incomplete')

    def test_probe_generator_does_not_need_historical_schedule(self):
        from evaluation.timing_analysis import generator_contract
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            record=dict(run_id='r',request_id='one',route='checkout',started_at=1,completed=True,outcome_ok=True)
            (path/'ledger.ndjson').write_text(json.dumps(record)+'\n')
            (path/'attempts.ndjson').write_text(json.dumps(record)+'\n')
            result=generator_contract(path,dict(run_id='r',probe_expected_checkout_operations=1),0,120)
            self.assertEqual(result['state'],'available')
            self.assertFalse(result['requires_historical_schedule_oracle'])

    def test_confirmation_retains_evidence_without_probe_count(self):
        from evaluation.timing_analysis import generator_contract
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            manifest=dict(kind='confirmation',probe_expected_checkout_operations=None)
            (path/'verification.json').write_text(json.dumps({'state':'incomplete','evidence':{'valid':True},'source':{'requests':360}}))
            result=generator_contract(path,manifest,0,300)
            self.assertEqual(result['state'],'available')
            self.assertEqual(result['observed_checkout_operations'],360)
            (path/'verification.json').write_text(json.dumps({'state':'pass'}))
            self.assertEqual(generator_contract(path,manifest,0,300)['state'],'incomplete')

    def test_probe_both_real_recipes_use_checkout_phase(self):
        from evaluation.timing_analysis import generator_contract
        from evaluation.workloads.timing_probe_contract import build_plan
        for demo in ('boutique','astronomy'):
            with self.subTest(demo=demo), tempfile.TemporaryDirectory() as directory:
                path=Path(directory)
                records=[]
                for journey in build_plan(demo,'r'):
                    for step in journey['steps']:
                        records.append(dict(step,run_id='r',started_at=step['planned_offset_seconds'],completed=True,outcome_ok=True))
                self.assertEqual(sum(r['route']=='checkout_expired' for r in records),12)
                for name in ('ledger.ndjson','attempts.ndjson'):
                    (path/name).write_text(''.join(json.dumps(r)+'\n' for r in records))
                result=generator_contract(path,dict(kind='probe',run_id='r',probe_expected_checkout_operations=24),0,120)
                self.assertEqual(result['observed_checkout_operations'],24)
                self.assertEqual(result['state'],'available')

    def test_required_evidence_gates_allow_numeric_failures(self):
        from evaluation.timing_analysis import required_evidence_gate
        checks={name:{'state':'fail','checks':[{'state':'fail'}]} for name in ('exact_request_count_api','exact_error_count_api','exact_duration_api')}
        report=dict(independent_valid=True,clock={'supports_1s_boundary_claims':True},
            availability_status={'valid':True},availability={'state':'available'},eventual_completeness={'exact':True},
            windows={name:{'api_storage_parity':{'checks':checks}} for name in ('whole','inner')})
        self.assertTrue(required_evidence_gate(report)['valid'])
        report['clock']['supports_1s_boundary_claims']=False
        self.assertFalse(required_evidence_gate(report)['valid'])
        report['clock']['supports_1s_boundary_claims']=True
        report['availability_status']['valid']=False
        self.assertFalse(required_evidence_gate(report)['valid'])
        report['availability_status']['valid']=True
        report['windows']['inner']['api_storage_parity']={'checks':{}}
        self.assertFalse(required_evidence_gate(report)['valid'])

    def test_eventual_error_semantics_preserve_unknown_mesh_status(self):
        def quiet(values,offset):
            return dict(stable=True,measured_collection_interval_seconds=15,records=[dict(observed_at=t+offset,values=values,sample_times={k:t+offset for k in values}) for t in (0,15,30)])
        trace_keys=[json.dumps(dict(source='traces.servicegraph',failed=value)) for value in ('false','true')]
        result=quiet_delta(quiet(dict.fromkeys(trace_keys,100),0),quiet({trace_keys[0]:120,trace_keys[1]:103},100),'traces.servicegraph')
        self.assertEqual(result['errors'],3)
        self.assertEqual(result['unknown_status_requests'],0)
        mesh_keys=[json.dumps(dict(source='mesh.istio',request_protocol='grpc',grpc_response_status=value)) for value in ('0','13','')]
        result=quiet_delta(quiet(dict.fromkeys(mesh_keys,100),0),quiet({mesh_keys[0]:120,mesh_keys[1]:103,mesh_keys[2]:104},100),'mesh.istio')
        self.assertIsNone(result['errors'])
        self.assertEqual(result['known_error_requests'],3)
        self.assertEqual(result['unknown_status_requests'],4)

    def test_captured_reset_in_drain_invalidates_positive_endpoint_delta(self):
        from evaluation.timing_analysis import audit_quiet_interval_counters
        metric=dict(__name__='servicegraph_request_count',source='traces.servicegraph',edge_client='frontend',edge_server='checkout',failed='false')
        key=json.dumps(metric,sort_keys=True,separators=(',',':'))
        def quiet(value,offset):
            return dict(stable=True,measured_collection_interval_seconds=15,records=[dict(observed_at=t+offset,values={key:value},sample_times={key:t+offset}) for t in (0,15,30)])
        before,after=quiet(100,0),quiet(200,100)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            # Reset60 is after a hypothetical fixed workload end45, before settled130.
            (path/'vm-raw.ndjson').write_text(json.dumps(dict(metric=metric,timestamps=[30000,60000,130000],values=[100,10,200]))+'\n')
            result=quiet_delta(before,after,'traces.servicegraph')
            self.assertTrue(result['exact'])
            audit_quiet_interval_counters(path,before,after,'traces.servicegraph',result)
            self.assertFalse(result['exact'])
            self.assertEqual(result['state'],'incomplete')
            self.assertIsNone(result['requests'])
            self.assertEqual(result['endpoint_requests_delta'],100)
            self.assertEqual(len(result['captured_resets']),1)


if __name__=='__main__': unittest.main()
