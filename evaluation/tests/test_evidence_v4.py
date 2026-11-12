import copy
import unittest

try:
    from evaluation import evidence_v4 as evidence
except ImportError:
    evidence = None


class ResourceEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(evidence, 'protocol4 exact-input verifier is missing')
        self.point=dict(namespace='ns',pod='pod',container='app',pod_uid='p-uid',node='n',
                        collector_uid='c-uid',timestamp=100.1,timestamp_ms=100100,
                        kind='cpu',value=1.25,unit='{cpu}',source='kubelet')
        self.input=dict(collector_uid='c-uid',requested_at_ns=100000000000,
                        received_at_ns=100050000000,path='/stats/summary',status=200,
                        body={'node':{'nodeName':'n'},'pods':[{'podRef':{'namespace':'ns','name':'pod','uid':'p-uid'},
                        'containers':[{'name':'app','cpu':{'time':90,'usageNanoCores':1250000000},
                                       'memory':{'time':90,'usageBytes':123456}}]}]})
        self.independent=dict(captured_at=101,summary=copy.deepcopy(self.input['body']))

    def test_known_cpu_conversion_and_same_underlying_reference(self):
        result=evidence.check_resource_input(self.point,[self.input],[self.independent],{})
        self.assertEqual(result['conversion']['state'],'pass')
        self.assertEqual(result['canonical_value'],1250)
        self.assertEqual(result['independent_agreement']['state'],'pass')
        self.assertEqual(result['measurement_timestamp'],90)
        self.assertAlmostEqual(result['cache_age_seconds'],10.05)

    def test_later_reading_cannot_replace_exact_input(self):
        self.independent['summary']['pods'][0]['containers'][0]['cpu']={'time':102,'usageNanoCores':5}
        result=evidence.check_resource_input(self.point,[self.input],[self.independent],{})
        self.assertEqual(result['conversion']['state'],'pass')
        self.assertEqual(result['independent_agreement']['state'],'incomplete')

    def test_memory_uses_usage_bytes_not_working_set(self):
        self.point.update(kind='memory',unit='By',value=123456)
        self.input['body']['pods'][0]['containers'][0]['memory']['workingSetBytes']=999
        result=evidence.check_resource_input(self.point,[self.input],[self.independent],{})
        self.assertEqual(result['conversion']['state'],'pass')
        self.assertEqual(result['canonical_value'],123456)

    def test_missing_input_and_multiple_scrapes_are_explicit(self):
        result=evidence.check_resource_input(self.point,[],[],{})
        self.assertEqual(result['conversion']['state'],'incomplete')
        duplicate=copy.deepcopy(self.input);duplicate['requested_at_ns']+=1000000000;duplicate['received_at_ns']+=1000000000
        result=evidence.check_resource_input(self.point,[self.input,duplicate],[],{})
        self.assertEqual(result['conversion']['state'],'incomplete')
        self.assertIn('ambiguous',result['conversion']['reason'])

    def test_wrong_collector_node_or_pod_lifecycle_cannot_match(self):
        for field,value in [('collector_uid','different'),('node','different'),('pod_uid','different')]:
            with self.subTest(field=field):
                p=dict(self.point,**{field:value})
                result=evidence.check_resource_input(p,[self.input],[self.independent],{})
                self.assertEqual(result['conversion']['state'],'incomplete')

    def test_conversion_disagreement_is_retained(self):
        self.point['value']=2
        result=evidence.check_resource_input(self.point,[self.input],[self.independent],{})
        self.assertEqual(result['conversion']['state'],'fail')


class TraceEvidenceTests(unittest.TestCase):
    def setUp(self):self.assertIsNotNone(evidence,'structured trace verifier is missing')

    def test_native_failures_and_unpaired_records_remain_separate(self):
        spans=[dict(trace_id='a',span_id='client',parent_id='outside',service='frontend',kind='client',
                    start=100,end=100.05,failed=False,attributes={}),
               dict(trace_id='a',span_id='server',parent_id='client',service='checkout',kind='server',
                    name='oteldemo.CheckoutService/PlaceOrder',start=100.01,end=100.04,failed=True,
                    attributes={'rpc.grpc.status_code':13}),
               dict(trace_id='b',span_id='server2',parent_id='missing',service='checkout',kind='server',
                    name='oteldemo.CheckoutService/PlaceOrder',start=101,end=101.02,failed=False,attributes={})]
        result=evidence.trace_accounting(spans,100,102)
        self.assertEqual(result['requests'],1)
        self.assertEqual(result['errors'],1)
        self.assertEqual(result['unpaired'],1)
        self.assertAlmostEqual(result['duration_ms'],30)
        self.assertEqual(result['grpc_outcome_errors'],1)

    def test_conflicting_span_identity_is_not_silently_overwritten(self):
        a=dict(trace_id='a',span_id='x',service='frontend',kind='client',start=100,end=101,failed=False,attributes={})
        result=evidence.trace_accounting([a,dict(a,end=102)],100,103)
        self.assertFalse(result['valid'])
        self.assertEqual(result['conflicting_spans'],1)



class ResourcePipelineIntegrationTests(unittest.TestCase):
    """Exercise the actual provider across on-disk OTLP and receiver evidence."""
    def fixture(self, directory, extra_active=False):
        import json
        from pathlib import Path
        path=Path(directory)
        raw=path/'structured/agent/raw';raw.mkdir(parents=True)
        (raw.parent/'pod.json').write_text(json.dumps({'metadata':{'uid':'collector-uid'},'spec':{'nodeName':'node'}}))
        app={'name':'app','cpu':{'time':90,'usageNanoCores':1250000000},
             'memory':{'time':90,'usageBytes':123456,'workingSetBytes':999}}
        other={'name':'istio-proxy','cpu':{'time':90,'usageNanoCores':1000000},'memory':{'time':90,'usageBytes':1024}}
        body={'node':{'nodeName':'node'},'pods':[{'podRef':{'namespace':'app-ns','name':'app-pod','uid':'pod-uid'},'containers':[app,other]}]}
        inventory=[{'metadata':{'name':'app-pod','namespace':'app-ns','uid':'pod-uid'},
            'spec':{'nodeName':'node','containers':[{'name':'app'}],
                'initContainers':[{'name':'init-done'},{'name':'istio-proxy','restartPolicy':'Always'}]},
            'status':{'containerStatuses':[{'name':'app','restartCount':0,'state':{'running':{'startedAt':'1970-01-01T00:01:00Z'}}}],
                'initContainerStatuses':[{'name':'init-done','restartCount':0,'state':{'terminated':{'finishedAt':'1970-01-01T00:01:20Z'}}},
                    {'name':'istio-proxy','restartCount':0,'state':{'running':{'startedAt':'1970-01-01T00:01:00Z'}}}]}}]
        if extra_active:
            body['pods'][0]['containers'].append(dict(app,name='active-missing'))
            inventory[0]['spec']['containers'].append({'name':'active-missing'})
            inventory[0]['status']['containerStatuses'].append({'name':'active-missing','restartCount':0,'state':{'running':{'startedAt':'1970-01-01T00:01:00Z'}}})
        receiver={'collector_uid':'collector-uid','path':'/stats/summary','status':200,
                  'requested_at_ns':100000000000,'received_at_ns':100050000000,'body':body}
        (raw/'receiver-input.ndjson').write_text(json.dumps(receiver)+'\n')
        resource={'k8s.namespace.name':'app-ns','k8s.pod.name':'app-pod','k8s.pod.uid':'pod-uid',
                  'k8s.container.name':'app','k8s.node.name':'node'}
        attributes=lambda value:[{'key':k,'value':{'stringValue':v}} for k,v in value.items()]
        metrics=[{'name':'container.cpu.usage','unit':'{cpu}','gauge':{'dataPoints':[{'timeUnixNano':'100100000000','asDouble':1.25,'attributes':attributes({'karthos_source':'kubelet'})}]}},
                 {'name':'container.memory.usage','unit':'By','gauge':{'dataPoints':[{'timeUnixNano':'100100000000','asInt':'123456','attributes':attributes({'karthos_source':'kubelet'})}]}}]
        (raw/'metrics.json').write_text(json.dumps({'resourceMetrics':[{'resource':{'attributes':attributes(resource)},'scopeMetrics':[{'metrics':metrics}]}]})+'\n')
        nodes=[{'id':'entity-app','kind':'container','namespace':'app-ns','labels':resource},
               {'id':'entity-init','kind':'container','namespace':'app-ns','labels':dict(resource,**{'k8s.container.name':'init-done'})},
               {'id':'entity-sidecar','kind':'container','namespace':'app-ns','labels':dict(resource,**{'k8s.container.name':'istio-proxy'})}]
        if extra_active:nodes.append({'id':'entity-missing','kind':'container','namespace':'app-ns','labels':dict(resource,**{'k8s.container.name':'active-missing'})})
        api=lambda value,unit:{'entity_id':'entity-app','timestamp':101.25,'source_timestamp':100.1,'value':value,'unit':unit,'labels':{'source':'kubelet'}}
        cpu=[api(1250,'millicores')];memory=[api(123456,'bytes')]
        stored=[{'metric':{'__name__':name,'entity_id':'entity-app','source':'kubelet','karthos_unit':unit},'timestamps':[100100],'values':[value]} for name,unit,value in [('k8s_container_cpu_usage','millicores',1250),('k8s_container_memory_usage','bytes',123456)]]
        independent=[{'captured_at':100.5,'received_at':100.5,'summary':copy.deepcopy(body)}]
        return path,nodes,cpu,memory,independent,stored,inventory

    def score(self, fixture):
        path,nodes,cpu,memory,independent,stored,inventory=fixture
        return evidence.resource_scores(path,nodes,cpu,memory,independent,'app-ns',100,110,{},stored,inventory)

    def test_exact_receiver_to_otlp_to_vm_to_api_passes_for_cpu_and_memory(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            result=self.score(self.fixture(directory))
        self.assertEqual(result['state'],'pass',result)
        self.assertEqual(result['expected_application_containers'],1)
        self.assertEqual({c['metric'] for c in result['checks']},{'cpu','memory'})
        for check in result['checks']:
            for stage in ('conversion','independent_agreement','preservation','freshness'):
                self.assertEqual(check[stage]['state'],'pass',check)
        self.assertEqual(result['overhead']['sidecar'],[['app-ns','app-pod','istio-proxy']])
        self.assertEqual(result['not_applicable'][0]['container'],['app-ns','app-pod','init-done'])

    def test_same_values_at_different_measurement_time_stay_incomplete(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            fixture=self.fixture(directory)
            for reading in fixture[4][0]['summary']['pods'][0]['containers']:
                for metric in ('cpu','memory'):reading[metric]['time']=90.005
            result=self.score(fixture)
        self.assertEqual(result['state'],'incomplete')
        for check in result['checks']:
            self.assertEqual(check['conversion']['state'],'pass')
            self.assertEqual(check['preservation']['state'],'pass')
            self.assertEqual(check['independent_agreement']['state'],'incomplete')
            self.assertIn('same-measurement',check['independent_agreement']['reason'])

    def test_missing_api_for_active_inventory_container_stays_incomplete(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            result=self.score(self.fixture(directory,extra_active=True))
        self.assertEqual(result['state'],'incomplete')
        self.assertEqual(result['expected_application_containers'],2)
        missing=[check for check in result['checks'] if check['container'][-1]=='active-missing']
        self.assertEqual({check['metric'] for check in missing},{'cpu','memory'})
        self.assertTrue(all(check['state']=='incomplete' and 'missing API' in check['reason'] for check in missing))

    def test_api_values_only_after_measurement_do_not_fill_active_window(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            fixture=self.fixture(directory)
            for sample in fixture[2]+fixture[3]:sample['timestamp']=120
            result=self.score(fixture)
        self.assertEqual(result['state'],'incomplete')
        self.assertTrue(all('missing API' in check.get('reason','') for check in result['checks']))

    def test_nanosecond_api_serialization_selects_same_vm_millisecond(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            fixture=self.fixture(directory)
            for sample in fixture[2]+fixture[3]:sample['timestamp']='1970-01-01T00:01:40.099999999Z'
            for series in fixture[5]:
                series['timestamps'].insert(0,100000);series['values'].insert(0,999)
            result=self.score(fixture)
        self.assertEqual(result['state'],'pass',result)
        for check in result['checks']:
            self.assertEqual(check['evaluation_timestamp_ms'],100100)
            self.assertEqual(check['stored_timestamp_ms'],100100)
            self.assertEqual(check['evaluation_serialized_timestamp'],'1970-01-01T00:01:40.099999999Z')
            self.assertAlmostEqual(check['evaluation_normalization_delta_seconds'],1e-9,places=15)

    def test_window_boundaries_use_the_same_explicit_vm_millisecond_identity(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path,nodes,cpu,memory,summaries,stored,inventory=self.fixture(directory)
            for sample in cpu+memory:sample['timestamp']='1970-01-01T00:01:40.099999999Z'
            at_start=evidence.resource_scores(path,nodes,cpu,memory,summaries,'app-ns',100.1,110.1,{},stored,inventory)
            for sample in cpu+memory:sample['timestamp']='1970-01-01T00:01:50.1Z'
            at_end=evidence.resource_scores(path,nodes,cpu,memory,summaries,'app-ns',100.1,evidence.stamp('1970-01-01T00:01:50.099999999Z'),{},stored,inventory)
        self.assertEqual(at_start['state'],'pass',at_start)
        self.assertEqual(at_end['state'],'pass',at_end)
        self.assertEqual(at_start['time_identity']['window_start_ms'],100100)
        self.assertEqual(at_end['time_identity']['window_end_ms'],110100)

    def test_overhead_categories_use_explicit_component_identity_without_changing_values(self):
        import tempfile
        from collections import defaultdict
        identities=[('karthos-system','karthos-agent-a','agent','karthos'),
                    ('karthos-system','karthos-agent-a','diag-reader','evaluation_capture'),
                    ('karthos-system','eval-capture-one','watchdog','evaluation_capture'),
                    ('app-ns','eval-otlp-relay-one','collector','source_collector'),
                    ('app-ns','otel-collector-agent-one','opentelemetry-collector','source_collector'),
                    ('kube-system','coredns-one','coredns','cluster_other'),
                    ('app-ns','eval-locust-one','generator','generator'),
                    ('app-ns','frontend-one','eval-observer','eval-observer')]
        with tempfile.TemporaryDirectory() as directory:
            fixture=self.fixture(directory)
            pods=fixture[4][0]['summary']['pods']
            for index,(ns,pod,container,category) in enumerate(identities,1):
                pods.append({'podRef':{'namespace':ns,'name':pod,'uid':pod+'-uid'},
                    'containers':[{'name':container,'cpu':{'time':90,'usageNanoCores':index*1000000},
                                   'memory':{'time':90,'usageBytes':index*1000}}]})
            result=self.score(fixture)
        self.assertEqual(result['state'],'pass')
        by_container={tuple(row['container']):row for row in result['overhead_metrics']}
        for index,(ns,pod,container,category) in enumerate(identities,1):
            row=by_container[(ns,pod,container)]
            self.assertEqual(row['category'],category)
            self.assertEqual(row['cpu_millicores_mean'],index)
            self.assertEqual(row['memory_usage_bytes_mean'],index*1000)
        grouped=defaultdict(list)
        for row in result['overhead_metrics']:grouped[row['category']].append(row['container'])
        self.assertEqual({k:sorted(v) for k,v in result['overhead'].items()}, {k:sorted(v) for k,v in grouped.items()})
        self.assertNotIn('collectors_karthos',result['overhead'])
        self.assertIn('observed usage',result['overhead_semantics'])
        self.assertIn('uninstrumented baseline',result['overhead_semantics'])

if __name__ == '__main__':unittest.main()
