import json
import tempfile
import unittest
from pathlib import Path
from evaluation.scoring import counter_window, weighted_duration, parse_spans, evaluate_window, resource_scores, mesh_source, DEFAULTS, aggregate_study, raw_api_contract, capture_integrity, oracle_contract, generator_artifact_completeness


class ScoringTests(unittest.TestCase):
    def test_counter_resets_deduplicates_and_requires_brackets(self):
        self.assertEqual(counter_window([(0, 10), (5, 15), (5, 15), (8, 2), (10, 5)], 0, 10)['increase'], 10)
        self.assertIsNone(counter_window([(2, 1), (8, 5)], 0, 10)['increase'])

    def test_weighted_failed_partitions_not_mean_of_means(self):
        samples = [{'labels': {'failed': 'false'}, 'value': 10, 'unit': 'milliseconds'},
                   {'labels': {'failed': 'true'}, 'value': 100, 'unit': 'milliseconds'}]
        self.assertEqual(weighted_duration(samples, {'false': 9, 'true': 1})['value_ms'], 19)
        self.assertIsNone(weighted_duration(samples, {})['value_ms'])

    def test_deduplicates_spans_and_preserves_pairing(self):
        raw = '''ResourceSpans #0
 -> service.name: Str(frontend)
Span #0
 Trace ID : abc
 Parent ID : root
 ID : client
 Name : /hipstershop.CheckoutService/PlaceOrder
 Kind : Client
 Start time : 2026-01-01 00:00:01 +0000 UTC
 End time : 2026-01-01 00:00:01.010 +0000 UTC
 Status code : Error
ResourceSpans #1
 -> service.name: Str(checkoutservice)
Span #0
 Trace ID : abc
 Parent ID : client
 ID : server
 Name : /hipstershop.CheckoutService/PlaceOrder
 Kind : Server
 Start time : 2026-01-01 00:00:01 +0000 UTC
 End time : 2026-01-01 00:00:01.009 +0000 UTC
 Status code : Error
'''
        spans = parse_spans(raw + raw)
        self.assertEqual(len(spans), 2)
        self.assertTrue(spans[('abc', 'server')]['failed'])

    def test_missing_source_oracle_incomplete_duplicate_attempts(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p/'manifest.json').write_text(json.dumps(dict(protocol_version=2,start=0,end=10,run_id='a',namespace='app',source_id='traces.servicegraph',mode='matched')))
            request = dict(request_id='r',run_id='a',started_at=2,status=200,outcome_ok=True)
            (p/'ledger.ndjson').write_text(json.dumps(request)+'\n')
            (p/'attempts.ndjson').write_text((json.dumps(request)+'\n')*2)
            result = evaluate_window(p,{})
            self.assertEqual(result['state'], 'incomplete')
            self.assertFalse(result['oracle_valid'])
            self.assertEqual(result['generator']['attempts'], 1)
            self.assertEqual(result['generator']['unterminated_attempts'], 0)

    def test_absent_container_is_incomplete_wrong_source_fails(self):
        nodes=[{'id':'id','kind':'container','namespace':'app','labels':{'k8s.pod.name':'p','k8s.container.name':'main'}}]
        summary=[{'captured_at':5,'summary':{'pods':[{'podRef':{'name':'p','namespace':'app'},'containers':[{'name':'main','cpu':{'usageNanoCores':10000000},'memory':{'usageBytes':42}}]}]}}]
        self.assertEqual(resource_scores(nodes,[],[],summary,'app',0,10,{})['state'],'incomplete')
        sample={'entity_id':'id','timestamp':5,'unit':'millicores','value':10,'labels':{'source':'wrong'}}
        self.assertEqual(resource_scores(nodes,[sample],[],summary,'app',0,10,{})['state'],'fail')

class AdditionalEvidenceTests(unittest.TestCase):
    def test_mesh_destination_only_resets_and_route_selection(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            records=[]
            for t,value in [(0,10),(5,15),(10,2)]:
                text='\n'.join('istio_requests_total{reporter="%s",destination_workload="checkout",source_workload="frontend",response_code="200",grpc_response_status="0"} %s' % (reporter,value) for reporter in ['source','destination'])
                records.append(dict(captured_at=t,pod='checkout-pod',text=text))
            (p/'istio-samples.ndjson').write_text('\n'.join(json.dumps(r) for r in records))
            result=mesh_source(p,0,10,DEFAULTS)
            self.assertEqual(result['requests'],7)
            self.assertEqual(len(result['series']),1)
            self.assertEqual(result['series'][0]['resets'],1)

    def test_resource_source_time_and_vm_api_stages(self):
        nodes=[{'id':'id','kind':'container','namespace':'app','labels':{'k8s.pod.name':'p','k8s.container.name':'main'}}]
        summary=[{'captured_at':2,'summary':{'pods':[{'podRef':{'name':'p','namespace':'app'},'containers':[{'name':'main','cpu':{'time':2,'usageNanoCores':10000000},'memory':{'time':2,'usageBytes':42,'workingSetBytes':1}}]}]}}]
        def sample(kind,value,unit): return dict(entity_id='id',timestamp=8,value=value,unit=unit,labels={'source':'kubelet'})
        stored=[dict(metric={'__name__':name,'entity_id':'id','source':'kubelet','karthos_unit':unit},timestamps=[2000],values=[value]) for name,value,unit in [('k8s_container_cpu_usage',10,'millicores'),('k8s_container_memory_usage',42,'bytes')]]
        result=resource_scores(nodes,[sample('cpu',10,'millicores')],[sample('memory',42,'bytes')],summary,'app',0,10,{},stored)
        self.assertEqual(result['state'],'pass')
        self.assertEqual(result['checks'][0]['stored_lag_seconds'],6)
        self.assertEqual(result['checks'][1]['reference_to_vm']['reference'],42)

    def test_empty_study_cannot_pass(self):
        with tempfile.TemporaryDirectory() as d:
            result=aggregate_study(Path(d))
            self.assertEqual(result['state'],'incomplete')
            self.assertEqual(result['absent_matched_windows'],48)

    def test_raw_api_numeric_mismatch_and_freshness(self):
        labels={'source':'traces.servicegraph','edge_client':'frontend','edge_server':'checkout','failed':'false'}
        samples=[dict(labels=labels,entity_id='id',timestamp=5,unit='count',value=11)]
        stored=[dict(metric=dict(labels,__name__='servicegraph_request_count',entity_id='id'),timestamps=[3000],values=[10])]
        result=raw_api_contract(samples,stored,'servicegraph_request_count','traces.servicegraph',DEFAULTS)
        self.assertEqual(result['state'],'fail')

class ReviewRegressionTests(unittest.TestCase):
    def test_demo_collectors_are_overhead(self):
        summary=[dict(captured_at=5,summary={'pods':[dict(podRef={'namespace':'app','name':'otel-collector-a'},containers=[dict(name='otel-collector',cpu={'time':5,'usageNanoCores':1},memory={'time':5,'usageBytes':1})]),dict(podRef={'namespace':'app','name':'eval-otlp-relay-a'},containers=[dict(name='relay')])]})]
        result=resource_scores([],[],[],summary,'app',0,10,{})
        self.assertEqual(result['expected_application_containers'],0)
        self.assertEqual(len(result['overhead']['collectors_karthos']),2)

    def test_stale_kubelet_underlying_time_cannot_pass(self):
        nodes=[dict(id='id',kind='container',namespace='app',labels={'k8s.pod.name':'p','k8s.container.name':'main'})]
        summary=[dict(captured_at=30,summary={'pods':[dict(podRef={'namespace':'app','name':'p'},containers=[dict(name='main',cpu={'time':1,'usageNanoCores':10000000},memory={'time':1,'usageBytes':42})])]})]
        def sample(v,u): return dict(entity_id='id',timestamp=30,value=v,unit=u,labels={'source':'kubelet'})
        vm=[dict(metric={'__name__':name,'entity_id':'id','source':'kubelet','karthos_unit':unit},timestamps=[30000],values=[v]) for name,v,unit in [('k8s_container_cpu_usage',10,'millicores'),('k8s_container_memory_usage',42,'bytes')]]
        result=resource_scores(nodes,[sample(10,'millicores')],[sample(42,'bytes')],summary,'app',0,35,{},vm)
        self.assertEqual(result['state'],'incomplete')
        self.assertEqual(result['checks'][0]['reference_to_vm']['kubelet_cache_age_seconds'],29)

    def test_oracle_requires_bidirectional_ids_and_marker_match(self):
        common=dict(terminals={'a':{}},attempted={'a':{}},conflicts=0,run_id_matches=True,capture_valid=True,source_valid=True,observer_boundaries=True)
        self.assertTrue(oracle_contract(**common))
        self.assertFalse(oracle_contract(**dict(common,terminals={'a':{},'b':{}})))
        self.assertFalse(oracle_contract(**dict(common,run_id_matches=False)))
        self.assertFalse(oracle_contract(**dict(common,conflicts=1)))
        # Outcome and pairing/count disagreement is deliberately outside harness validity.

    def test_capture_early_observer_exit_and_sampler_errors_invalid(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            streams=[dict(stream='app__frontend-a__eval-observer',exited_before_stop=False),dict(stream='app__checkout-a__eval-observer',exited_before_stop=False),dict(stream='app__eval-otlp-relay-a__relay',exited_before_stop=False)]
            (p/'capture-status.json').write_text(json.dumps(dict(sampler_stopped=True,streams=streams)))
            self.assertTrue(capture_integrity(p,'traces.servicegraph')['valid'])
            streams[0]['exited_before_stop']=True
            (p/'capture-status.json').write_text(json.dumps(dict(sampler_stopped=True,streams=streams)))
            self.assertFalse(capture_integrity(p,'traces.servicegraph')['valid'])
            streams[0]['exited_before_stop']=False
            (p/'capture-errors.ndjson').write_text(json.dumps({'error':'sampler failed'})+'\n')
            self.assertFalse(capture_integrity(p,'traces.servicegraph')['valid'])

class AstronomyCaptureRegressionTests(unittest.TestCase):
    def test_astronomy_agent_is_selected_and_proxy_excluded(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            streams=[dict(stream='app__frontend-a__eval-observer',exited_before_stop=False),dict(stream='app__checkout-a__eval-observer',exited_before_stop=False),dict(stream='app__otel-collector-agent-a__otelcol',exited_before_stop=False),dict(stream='app__otel-collector-agent-a__istio-proxy',exited_before_stop=True)]
            (p/'capture-status.json').write_text(json.dumps(dict(sampler_stopped=True,streams=streams)))
            result=capture_integrity(p,'traces.servicegraph',demo='astronomy')
            self.assertTrue(result['valid'])
            self.assertEqual(len(result['selected_streams']),3)
            streams[2]['exited_before_stop']=True
            (p/'capture-status.json').write_text(json.dumps(dict(sampler_stopped=True,streams=streams)))
            self.assertFalse(capture_integrity(p,'traces.servicegraph',demo='astronomy')['valid'])

class WindowOracleRegressionTests(unittest.TestCase):
    def test_complete_capture_keeps_numeric_failure_scoreable_but_interruption_invalid(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            manifest=dict(protocol_version=2,start=0,end=10,run_id='r',namespace='app',source_id='traces.servicegraph',mode='matched',generator_job_failed=True)
            (p/'manifest.json').write_text(json.dumps(manifest))
            request=dict(request_id='a',run_id='r',started_at=2,status=500,outcome_ok=False)
            for name in ('ledger','attempts'): (p/(name+'.ndjson')).write_text(json.dumps(request)+'\n')
            (p/'generator-window.json').write_text(json.dumps([dict(run_id='r',ended_at=10)]))
            (p/'k6.ndjson').write_text(json.dumps({'type':'Point','metric':'dropped_iterations','data':{'value':4}})+'\n')
            observed=[dict(boundary='frontend',run_id='r',request_id='a',status=200),dict(boundary='checkout',uri='/hipstershop.CheckoutService/PlaceOrder',status=200,grpc_status='0',request_time_seconds='.01')]
            source=dict(valid=True,requests=2,errors=0,duration_ms=10,unpaired=1)
            with patch('evaluation.scoring.capture_integrity',return_value=dict(valid=True,state='pass',reasons=[])),patch('evaluation.scoring.observer_records',return_value=observed),patch('evaluation.scoring.trace_source',return_value=source):
                result=evaluate_window(p,{})
                self.assertTrue(result['oracle_valid'])
                self.assertEqual(result['generator']['dropped_iterations'],4)
                self.assertEqual(result['state'],'fail')
                self.assertIn('source_to_vm',result['duration'])
                self.assertIn('vm_to_api',result['duration'])
                manifest['generator_job_failed']=False
                request['outcome_ok']=True
                observed[0]['status']=500
                for name in ('ledger','attempts'): (p/(name+'.ndjson')).write_text(json.dumps(request)+'\n')
                (p/'manifest.json').write_text(json.dumps(manifest))
                dropped_only=evaluate_window(p,{})
                self.assertTrue(dropped_only['oracle_valid'])
                self.assertEqual(dropped_only['state'],'fail')
                (p/'k6.ndjson').unlink()
                self.assertEqual(evaluate_window(p,{})['state'],'incomplete')
                manifest['interrupted']=True
                (p/'manifest.json').write_text(json.dumps(manifest))
                self.assertFalse(evaluate_window(p,{})['oracle_valid'])
                manifest.pop('interrupted')
                (p/'manifest.json').write_text(json.dumps(manifest))
                (p/'generator-window.json').write_text(json.dumps([dict(run_id='r',ended_at=None)]))
                self.assertFalse(evaluate_window(p,{})['oracle_valid'])

class ResourcePrerollRegressionTests(unittest.TestCase):
    def test_preroll_aligns_allowed_vm_cache_without_inflating_overhead(self):
        nodes=[dict(id='id',kind='container',namespace='app',labels={'k8s.pod.name':'main-pod','k8s.container.name':'main'})]
        def snapshot(t,generator_cpu):
            return dict(captured_at=t,summary={'pods':[
                dict(podRef={'namespace':'app','name':'main-pod'},containers=[dict(name='main',cpu={'time':t,'usageNanoCores':5000000},memory={'time':t,'usageBytes':42})]),
                dict(podRef={'namespace':'app','name':'eval-k6-pod'},containers=[dict(name='k6',cpu={'time':t,'usageNanoCores':generator_cpu*1000000},memory={'time':t,'usageBytes':100})])]})
        summaries=[snapshot(60,1000),snapshot(100,10)]
        def sample(v,u): return dict(entity_id='id',timestamp=100,value=v,unit=u,labels={'source':'kubelet'})
        def stored(t): return [dict(metric={'__name__':name,'entity_id':'id','source':'kubelet','karthos_unit':unit},timestamps=[t*1000],values=[v]) for name,v,unit in [('k8s_container_cpu_usage',5,'millicores'),('k8s_container_memory_usage',42,'bytes')]]
        result=resource_scores(nodes,[sample(5,'millicores')],[sample(42,'bytes')],summaries,'app',100,110,{},stored(60))
        self.assertEqual(result['state'],'pass')
        self.assertEqual(result['checks'][0]['reference_to_vm']['reference_captured_at'],60)
        self.assertEqual(result['checks'][0]['stored_lag_seconds'],40)
        footprint=result['overhead_metrics'][0]
        self.assertEqual(footprint['samples'],1)
        self.assertEqual(footprint['cpu_millicores_mean'],10)
        stale=resource_scores(nodes,[sample(5,'millicores')],[sample(42,'bytes')],[snapshot(39,1000),snapshot(100,10)],'app',100,110,{},stored(39))
        self.assertEqual(stale['state'],'incomplete')
        self.assertEqual(stale['checks'][0]['vm_to_api']['reason'],'stored sample stale')

class NativeExecutionRegressionTests(unittest.TestCase):
    def test_failed_native_job_retains_outputs_but_is_not_complete(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            manifest=dict(start=0,end=10,mode='native',namespace='app',source_id='traces.servicegraph',generator='locust',generator_job_failed=True)
            (p/'manifest.json').write_text(json.dumps(manifest))
            for name in ('generator.log','generator-version.txt','locust-report.html'): (p/name).write_text('retained')
            (p/'locust-stats.csv').write_text('Type,Name,Request Count,Failure Count\n,Aggregated,100,5\n')
            for name in ('api-nodes.json','api-cpu.json','api-memory.json'): (p/name).write_text(json.dumps({'data':{}}))
            (p/'kubelet.ndjson').write_text(json.dumps({'captured_at':2,'summary':{'pods':[]}})+'\n')
            with patch('evaluation.scoring.capture_integrity',return_value=dict(valid=True,state='pass',reasons=[])):
                failed=evaluate_window(p,{})
                self.assertFalse(failed['native_evidence']['complete'])
                self.assertTrue(failed['native_evidence']['execution_failed'])
                self.assertEqual(failed['native_evidence']['locust_request_failures'],5)
                self.assertEqual((p/'locust-report.html').read_text(),'retained')
                manifest['generator_job_failed']=False
                (p/'manifest.json').write_text(json.dumps(manifest))
                completed=evaluate_window(p,{})
                self.assertTrue(completed['native_evidence']['complete'])
                self.assertEqual(completed['native_evidence']['locust_request_failures'],5)
                # Derived native outputs cannot hide a corrupt opt-in raw archive.
                import types
                from unittest.mock import Mock
                manifest['artifact_protocol']=1
                (p/'manifest.json').write_text(json.dumps(manifest))
                (p/'generator-artifact-status.json').write_text(json.dumps(dict(verified=True,semantic_complete=True)))
                module=types.ModuleType('evaluation.generator_artifacts')
                module.validate_export=Mock(side_effect=ValueError('raw native archive missing'))
                with patch.dict('sys.modules',{'evaluation.generator_artifacts':module}):
                    corrupt=evaluate_window(p,{})
                self.assertFalse(corrupt['native_evidence']['complete'])
                self.assertFalse(corrupt['generator_artifacts']['valid'])
                self.assertTrue((p/'locust-report.html').exists())

                manifest['native_boundary_error']='missing terminated timestamps'
                (p/'manifest.json').write_text(json.dumps(manifest))
                self.assertFalse(evaluate_window(p,{})['native_evidence']['complete'])

class GeneratorArtifactGateTests(unittest.TestCase):
    def validator_module(self,validator):
        import types
        module=types.ModuleType('evaluation.generator_artifacts')
        module.validate_export=validator
        return module

    def test_optin_rechecks_raw_export_and_requires_saved_status(self):
        from unittest.mock import Mock,patch
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); manifest=dict(artifact_protocol=1,generator='k6',mode='matched',run_id='r')
            validator=Mock(return_value=dict(verified=True,semantic_complete=True,workload_exit_code=0))
            with patch.dict('sys.modules',{'evaluation.generator_artifacts':self.validator_module(validator)}):
                self.assertFalse(generator_artifact_completeness(p,manifest)['valid'])
                (p/'generator-artifact-status.json').write_text(json.dumps(dict(verified=True,semantic_complete=True)))
                self.assertTrue(generator_artifact_completeness(p,manifest)['valid'])
                validator.assert_called_with(p/'generator-artifacts','k6','matched','r')
                validator.side_effect=ValueError('raw checksum mismatch')
                invalid=generator_artifact_completeness(p,manifest)
                self.assertFalse(invalid['valid'])
                self.assertIn('raw checksum mismatch',' '.join(invalid['reasons']))
                self.assertIsNone(generator_artifact_completeness(p,dict(manifest,artifact_protocol=0)))

    def test_corrupt_raw_invalidates_oracle_but_workload99_remains_scoreable(self):
        from unittest.mock import Mock,patch
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); manifest=dict(start=0,end=10,mode='matched',run_id='r',source_id='traces.servicegraph',namespace='app',artifact_protocol=1,generator='k6',generator_job_failed=True)
            (p/'manifest.json').write_text(json.dumps(manifest))
            request=dict(request_id='a',run_id='r',started_at=2,status=200,outcome_ok=True)
            for name in ('ledger','attempts'): (p/(name+'.ndjson')).write_text(json.dumps(request)+'\n')
            (p/'generator-window.json').write_text(json.dumps([dict(run_id='r',ended_at=10)]))
            (p/'generator-artifact-status.json').write_text(json.dumps(dict(verified=True,semantic_complete=True)))
            raw=p/'generator-artifacts'; raw.mkdir(); (raw/'workload-exit-code.txt').write_text('99\n')
            (p/'k6.ndjson').write_text(json.dumps({'type':'Point','metric':'dropped_iterations','data':{'value':0}})+'\n')
            observed=[dict(boundary='frontend',run_id='r',request_id='a',status=200),dict(boundary='checkout',uri='/hipstershop.CheckoutService/PlaceOrder',status=200,grpc_status='0',request_time_seconds='.01')]
            validator=Mock(return_value=dict(verified=True,semantic_complete=True))
            with patch.dict('sys.modules',{'evaluation.generator_artifacts':self.validator_module(validator)}),patch('evaluation.scoring.capture_integrity',return_value=dict(valid=True,state='pass',reasons=[])),patch('evaluation.scoring.observer_records',return_value=observed),patch('evaluation.scoring.trace_source',return_value=dict(valid=True,requests=1,errors=0,duration_ms=10,unpaired=0)):
                good=evaluate_window(p,{})
                self.assertTrue(good['oracle_valid'])
                self.assertEqual(good['state'],'fail')
                self.assertEqual(good['generator']['workload_exit_code'],99)
                self.assertTrue(any(x['stage']=='generator_artifact_completeness' for x in good['stage_table']))
                validator.side_effect=ValueError('missing raw k6.ndjson')
                bad=evaluate_window(p,{})
                self.assertFalse(bad['oracle_valid'])
                self.assertFalse(bad['generator_artifacts']['valid'])
                validator.side_effect=None
                (raw/'workload-exit-code.txt').write_text('0\n')
                held=evaluate_window(p,{})
                self.assertTrue(held['oracle_valid'])
                self.assertFalse(held['generator']['job_failed'])
                self.assertNotEqual(held['state'],'fail')
                manifest.pop('artifact_protocol'); (p/'manifest.json').write_text(json.dumps(manifest))
                legacy=evaluate_window(p,{})
                self.assertTrue(legacy['oracle_valid'])
                self.assertFalse(any(x['stage']=='generator_artifact_completeness' for x in legacy['stage_table']))

class AggregateCoverageRegressionTests(unittest.TestCase):
    def test_matched_coverage_without_native_cannot_pass(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for repeat in (1,2,3):
                for demo in ('boutique','astronomy'):
                    for source in ('mesh','trace'):
                        for generator in ('k6','locust'):
                            for workload in ('legacy','checkout-heavy'):
                                p=root/'measured'/f'r{repeat}-{demo}-{source}'/'windows'/f'{generator}-{workload}'
                                p.mkdir(parents=True); (p/'manifest.json').write_text('{}')
            def report(p,thresholds):
                cell=p.parent.parent.name; gen,work=p.name.split('-',1)
                return dict(window=str(p),descriptive_only=False,state='pass',source_valid=True,manifest=dict(cell=cell,generator=gen,workload=work),generator={},source={'requests':1,'errors':0},counts_path={},resources={'overhead_metrics':[]})
            with patch('evaluation.scoring.evaluate_window',side_effect=report):
                result=aggregate_study(root)
            self.assertEqual(result['state'],'incomplete')
            self.assertEqual(result['absent_native_windows'],4)
            def duplicate_report(p,thresholds):
                r=report(p,thresholds); r['manifest']['cell']='r1-boutique-trace'; return r
            with patch('evaluation.scoring.evaluate_window',side_effect=duplicate_report):
                result=aggregate_study(root)
            self.assertEqual(result['state'],'incomplete')
            self.assertGreater(len(result['missing_matched_window_ids']),0)

if __name__ == '__main__': unittest.main()

class ProtocolThreeTests(unittest.TestCase):
    def test_grpc_status_is_full_series_identity(self):
        labels=dict(source='mesh.istio',edge_client='frontend',edge_server='checkout',**{'http.response.status_code':'200','rpc.grpc.status_code':'13'})
        sample=dict(labels=labels,entity_id='id',timestamp=5,unit='count',value=131)
        rows=[dict(metric=dict(labels,**{'rpc.grpc.status_code':status},__name__='servicegraph_request_count',entity_id='id',karthos_unit='count'),timestamps=[4000],values=[value]) for status,value in [('0',718),('13',131)]]
        self.assertEqual(raw_api_contract([sample],rows,'servicegraph_request_count','mesh.istio',DEFAULTS)['state'],'pass')

    def test_ambiguous_same_timestamp_not_largest_value(self):
        labels=dict(source='mesh.istio',edge_client='frontend',edge_server='checkout')
        sample=dict(labels=labels,entity_id='id',timestamp=5,unit='count',value=9)
        vm=[dict(metric=dict(labels,__name__='servicegraph_request_count',entity_id='id'),timestamps=[4000,4000],values=[7,9])]
        result=raw_api_contract([sample],vm,'servicegraph_request_count','mesh.istio',DEFAULTS)
        self.assertEqual(result['state'],'incomplete')
        self.assertIn('ambiguous',result['checks'][0]['reason'])

    def test_weighted_status_partitions_and_same_window(self):
        from evaluation.series_identity import partition_key
        labels=[{'source':'mesh.istio','rpc.grpc.status_code':v} for v in ('0','13')]
        durations=[dict(labels=l,value=v,unit='milliseconds',timestamp=10) for l,v in zip(labels,[10,100])]
        weights=[dict(labels=l,value=v,unit='count',timestamp=10) for l,v in zip(labels,[9,1])]
        self.assertEqual(weighted_duration(durations,weights)['value_ms'],19)
        weights[0]['timestamp']=9
        self.assertEqual(weighted_duration(durations,weights)['state'],'incomplete')

    def test_completed_init_not_expected_but_restartable_is_overhead(self):
        from evaluation.series_identity import container_lifecycle
        pod=dict(metadata=dict(namespace='app',name='p'),spec=dict(containers=[dict(name='main')],initContainers=[dict(name='setup'),dict(name='mesh',restartPolicy='Always')]),status=dict(initContainerStatuses=[dict(name='setup',state=dict(terminated=dict(finishedAt='1970-01-01T00:00:01Z')))]))
        lifecycle=container_lifecycle([pod],2,10)
        self.assertFalse(lifecycle[('app','p','setup')]['expected'])
        self.assertEqual(lifecycle[('app','p','mesh')]['category'],'sidecar')
        nodes=[dict(id=n,kind='container',namespace='app',labels={'k8s.pod.name':'p','k8s.container.name':n}) for n in ['main','setup','mesh']]
        result=resource_scores(nodes,[],[],[],'app',2,10,{},inventory=[pod])
        self.assertEqual(result['expected_application_containers'],1)
        self.assertEqual(len(result['not_applicable']),1)

    def test_outcome_policy_keeps_missing_grpc_unknown(self):
        from evaluation.series_identity import request_failed
        self.assertTrue(request_failed({'request_protocol':'grpc','response_code':'200','grpc_response_status':'13'}))
        self.assertIsNone(request_failed({'request_protocol':'grpc','response_code':'200'}))
        self.assertFalse(request_failed({'response_code':'404'}))
        self.assertTrue(request_failed({'response_code':'500'}))

    def test_histogram_partitions_must_have_matching_sum_count(self):
        from evaluation.scoring import histogram_mean
        base={'source':'mesh.istio','edge_client':'frontend','edge_server':'checkout','karthos_unit':'milliseconds'}
        series=[dict(metric=dict(base,__name__='servicegraph_request_duration_sum',**{'rpc.grpc.status_code':'0'}),increase=90),dict(metric=dict(base,__name__='servicegraph_request_duration_count',**{'rpc.grpc.status_code':'13'}),increase=9)]
        self.assertEqual(histogram_mean(series)['state'],'incomplete')
        series[1]['metric']['rpc.grpc.status_code']='0'
        self.assertEqual(histogram_mean(series)['value_ms'],10)

    def test_exact_query_parity_preserves_status_and_time(self):
        from evaluation.scoring import exact_query_parity
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);(p/'vm-parity').mkdir()
            labels={'source':'mesh.istio','edge_client':'frontend','edge_server':'checkout','rpc.grpc.status_code':'13'}
            api=dict(labels=labels,entity_id='id',timestamp=5,unit='count',value=2)
            (p/'api-request-count.json').write_text(json.dumps({'data':{'samples':[api]}}))
            vm=dict(status='success',data=dict(resultType='matrix',result=[dict(metric=dict(labels,entity_id='id',karthos_unit='count'),values=[[5,'2']])]))
            (p/'vm-parity/api-request-count.json').write_text(json.dumps({'response':vm}))
            self.assertEqual(exact_query_parity(p,'api-request-count.json','mesh.istio')['state'],'pass')
            vm['data']['result'][0]['metric']['rpc.grpc.status_code']='0'
            (p/'vm-parity/api-request-count.json').write_text(json.dumps({'response':vm}))
            self.assertEqual(exact_query_parity(p,'api-request-count.json','mesh.istio')['state'],'incomplete')

    def test_counter_rejects_ambiguous_boundary_values_and_delta(self):
        self.assertIsNone(counter_window([(0,1),(0,2),(10,5)],0,10)['increase'])
        self.assertIsNone(counter_window([(0,1),(10,5)],0,10,temporality='delta')['increase'])

    def test_mesh_query_view_ignores_legacy_derived_error_counter(self):
        from evaluation.scoring import vm_source
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)
            labels=dict(source='mesh.istio',edge_client='frontend',edge_server='checkout',**{'network.protocol.name':'grpc','http.response.status_code':'200'})
            vm=[]
            for name,status,values in [('servicegraph_request_count','0',[80,89]),('servicegraph_request_count','13',[10,12]),('servicegraph_request_error_count','13',[100,150])]:
                vm.append(dict(metric=dict(labels,__name__=name,**{'rpc.grpc.status_code':status}),timestamps=[0,10000],values=values))
            (p/'vm-raw.ndjson').write_text('\n'.join(json.dumps(x) for x in vm))
            result=vm_source(p,0,10,'mesh.istio',DEFAULTS)
            self.assertEqual(result['errors'],2)

class TraceConnectorFailureContractTests(unittest.TestCase):
    def test_connector_failure_is_or_of_span_statuses_not_raw_grpc(self):
        from evaluation.scoring import trace_source
        template='''ResourceSpans #0
 -> service.name: Str(frontend)
Span #0
 Trace ID : abc
 ID : client
 Name : /hipstershop.CheckoutService/PlaceOrder
 Kind : Client
 Start time : 1970-01-01 00:00:01 +0000 UTC
 End time : 1970-01-01 00:00:01.010 +0000 UTC
 Status code : CLIENTSTATUS
ResourceSpans #1
 -> service.name: Str(checkoutservice)
Span #0
 Trace ID : abc
 Parent ID : client
 ID : server
 Name : /hipstershop.CheckoutService/PlaceOrder
 Kind : Server
 Start time : 1970-01-01 00:00:01 +0000 UTC
 End time : 1970-01-01 00:00:01.009 +0000 UTC
 Status code : SERVERSTATUS
 -> rpc.grpc.status_code: Int(GRPCSTATUS)
'''
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);(path/'logs').mkdir()
            for client,server,grpc,expected in [('Error','Unset','0',1),('Unset','Error','0',1),('Error','Error','13',1),('Unset','Unset','13',0)]:
                with self.subTest(client=client,server=server,grpc=grpc):
                    raw=template.replace('CLIENTSTATUS',client).replace('SERVERSTATUS',server).replace('GRPCSTATUS',grpc)
                    (path/'logs/relay.log').write_text(raw)
                    result=trace_source(path,0,10)
                    self.assertEqual(result['errors'],expected)
                    self.assertEqual(result['spans'][0]['grpc_outcome_failed'],grpc!='0')

class ExactParityCompletenessTests(unittest.TestCase):
    def test_api_cannot_omit_one_vm_status_partition(self):
        from evaluation.scoring import exact_query_parity
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);(p/'vm-parity').mkdir()
            labels={'source':'mesh.istio','edge_client':'frontend','edge_server':'checkout','rpc.grpc.status_code':'0'}
            api=dict(labels=labels,entity_id='id',timestamp=5,unit='count',value=9)
            (p/'api-request-count.json').write_text(json.dumps({'data':{'samples':[api]}}))
            results=[dict(metric=dict(labels,entity_id='id'),values=[[5,'9']]),dict(metric=dict(labels,entity_id='id',**{'rpc.grpc.status_code':'13'}),values=[[5,'2']])]
            vm=dict(status='success',data=dict(resultType='matrix',result=results))
            (p/'vm-parity/api-request-count.json').write_text(json.dumps({'response':vm}))
            result=exact_query_parity(p,'api-request-count.json','mesh.istio')
            self.assertEqual(result['state'],'incomplete')
            self.assertEqual(len(result['missing_api_partitions']),1)
