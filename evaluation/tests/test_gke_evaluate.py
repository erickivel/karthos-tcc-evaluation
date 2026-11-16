import contextlib
import importlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]


class EvaluationTests(unittest.TestCase):
    def module(self):
        try:return importlib.import_module('evaluation.gke.evaluate')
        except ImportError:self.fail('per-window GKE evaluator is missing')

    def observed(self,**changes):
        return dict({'metric':'system.memory.usage','value':2048,'unit':'bytes','source':'host-os','node':'n1','node_uid':'node-1',
                     'dimensions':{'state':'free'},'measurement_timestamp':5,'kind':'gauge'},**changes)

    def reference(self,**changes):
        return dict({'metric':'memory.MemFree','value':2,'unit':'kB','source':'linux-proc','node':'n1','node_uid':'node-1',
                     'boot_id':'boot','observer_pod_uid':'observer','dimensions':{},'measurement_timestamp':5,
                     'acquisition_started_at':4.9,'acquisition_ended_at':5.1},**changes)

    def test_gauge_conversion_is_exact_and_requires_source_time_identity(self):
        m=self.module();result=m.compare_reference(self.observed(),[self.reference()])
        self.assertEqual(result['state'],'pass',result);self.assertEqual(result['reference_value'],2048)
        self.assertEqual(result['conversion_factor'],1024)
        self.assertEqual(m.compare_reference(self.observed(),[self.reference(measurement_timestamp=None)])['state'],'insufficient')

    def test_dimension_ambiguity_and_missing_or_changed_lifecycle_do_not_choose_max(self):
        m=self.module()
        for refs in ([],[self.reference(),self.reference(value=3)],[self.reference(node_uid='replacement')], [self.reference(boot_id=None)]):
            self.assertEqual(m.compare_reference(self.observed(),refs)['state'],'insufficient')
        self.assertEqual(m.compare_reference(self.observed(dimensions={'state':'free','unexpected':'x'}),[self.reference()])['state'],'insufficient')

    def test_monotonic_brackets_retain_actual_times_and_reset_is_insufficient(self):
        m=self.module();observed=self.observed(metric='system.network.io.receive',kind='sum',is_monotonic=True,aggregation_temporality=2,value=150,dimensions={'interface':'eth0','direction':'receive'})
        refs=[self.reference(metric='network.bytes',unit='bytes',value=value,dimensions={'interface':'eth0','direction':'receive'},measurement_timestamp=None,acquisition_started_at=a,acquisition_ended_at=b) for value,a,b in [(100,1,2),(200,8,9)]]
        result=m.compare_reference(observed,refs)
        self.assertEqual(result['state'],'pass',result);self.assertEqual(result['reference_bounds'],[100,200]);self.assertEqual(result['reference_acquisition_brackets'],[[1,2],[8,9]])
        refs[1]['value']=50
        self.assertEqual(m.compare_reference(observed,refs)['state'],'insufficient')
        refs[1]['value']=200;refs[1]['boot_id']='replacement'
        self.assertEqual(m.compare_reference(observed,refs)['state'],'insufficient')

    def test_numeric_mismatch_is_result_separate_from_capture_validity(self):
        m=self.module();self.assertEqual(m.compare_reference(self.observed(value=2049),[self.reference()])['state'],'fail')

    def test_coverage_always_reports_all_43_and_never_counts_catalog_only_as_observed(self):
        m=self.module();coverage=json.loads((ROOT/'protocols/gke-qualification-v1/coverage.yaml').read_text())
        report=m.catalog_coverage(coverage,{'data':{'metrics':[{'name':x['name']} for x in coverage['entries']]}},{},{},[],0,10,{})
        self.assertEqual(len(report['entries']),43)
        self.assertTrue(all(x['classification']!='observed' for x in report['entries']))

    def test_full_dimension_source_and_staleness_are_required_for_observed_coverage(self):
        m=self.module();coverage={'expected_count':1,'entries':[{'name':'system.memory.usage'}]};catalog={'data':{'metrics':[{'name':'system.memory.usage'}]}}
        sample={'entity_id':'e','value':2048,'timestamp':9,'labels':{'source':'host-os','state':'free'}}
        api={'system.memory.usage':{'data':{'samples':[sample]}}};stored=[{'metric':{'__name__':'system_memory_usage','entity_id':'e','source':'host-os','state':'free'},'timestamps':[9000],'values':[2048]}]
        result=m.catalog_coverage(coverage,catalog,{'metric_assignments':{'system.memory.usage':'host-os'}},api,stored,0,10,{})
        self.assertEqual(result['entries'][0]['classification'],'observed',result)
        stored[0]['metric']['state']='used'
        self.assertNotEqual(m.catalog_coverage(coverage,catalog,{'metric_assignments':{'system.memory.usage':'host-os'}},api,stored,0,10,{})['entries'][0]['classification'],'observed')

    def test_extended_capture_preserves_all_pages_and_raw_vm_without_live_commands(self):
        m=self.module();coverage=json.loads((ROOT/'protocols/gke-qualification-v1/coverage.yaml').read_text());calls=[]
        def pages(base,route,params,key,pages_dir=None):
            calls.append((route,params,key));return {'data':{key:[{'name':x['name']} for x in coverage['entries']] if key=='metrics' else []}}
        with tempfile.TemporaryDirectory() as d:
            ctx=SimpleNamespace(root=ROOT,manifest={'config':{}},json=lambda *a:{'items':[{'metadata':{'name':'victoria'}}]} if 'services' in a else {'data':{'source-plan.json':'{"metric_assignments":{}}'}},write=lambda p,v:m.write(p,v))
            with patch('evaluation.gke.parity.capture',return_value={'metrics':[]}),patch.object(m.r,'forward',side_effect=lambda *a:contextlib.nullcontext('http://fixture')),patch.object(m.r,'api_pages',side_effect=pages),patch.object(m.r,'get_json',return_value={'data':{}}),patch.object(m.r.urllib.request,'urlopen',return_value=contextlib.nullcontext(SimpleNamespace(read=lambda *a:b'{"metric":{"__name__":"x"},"timestamps":[1000],"values":[1]}\n'))):
                result=m.capture_extended(ctx,SimpleNamespace(namespace='app',source='trace'),Path(d),0,10)
            self.assertEqual(len([x for x in calls if x[0]=='/api/v1/metrics']),43)
            self.assertTrue((Path(d)/'extended/vm-raw.ndjson').exists());self.assertEqual(result['state'],'complete',result)

    def test_restart_execution_does_not_claim_invalid_traffic_oracle(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as d:
            path=Path(d);m.write(path/'manifest.json',{'start':0,'end':10});m.write(path/'scenario-result.json',{'state':'complete'})
            ctx=SimpleNamespace(root=ROOT,manifest={'thresholds':{},'config':{}},write=m.write)
            report=m.evaluate(ctx,{'phase':'restart','scenario':'gateway'},path,traffic_report={'independent_valid':False,'state':'incomplete'})
            self.assertEqual(report['execution']['state'],'complete');self.assertFalse(report['traffic']['oracle_valid'])
            self.assertNotEqual(report['evidence']['state'],'pass')
            self.assertEqual(len(report['coverage']['entries']),43)

    def test_raw_otlp_timestamp_is_not_fabricated_as_host_measurement_alignment(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'agent';(root/'raw').mkdir(parents=True)
            attrs=lambda values:[{'key':key,'value':{'stringValue':value}} for key,value in values.items()]
            m.write(root/'raw/metrics.json',json.dumps({'resourceMetrics':[{'resource':{'attributes':attrs({'k8s.node.name':'n1'})},'scopeMetrics':[{'metrics':[{'name':'system.memory.usage','unit':'By','gauge':{'dataPoints':[{'asInt':'2048','timeUnixNano':'5000000000','attributes':attrs({'state':'free','karthos_source':'host-os'})}]}}]}]}]})+'\n')
            observations=m._host_observations(Path(d),{'agents':[{'root':str(root),'node_uid':'node-1','collector_uid':'collector'}]})
            self.assertIsNone(observations[0]['measurement_timestamp'])
            self.assertEqual(observations[0]['recorded_timestamp'],5)
            self.assertEqual(m.compare_reference(observations[0],[self.reference()])['state'],'insufficient')

    def test_malformed_export_still_returns_explicit_incomplete_result(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as d:
            path=Path(d);(path/'extended').mkdir();(path/'extended/vm-raw.ndjson').write_text('{not-json}\n')
            ctx=SimpleNamespace(root=ROOT,manifest={'thresholds':{},'config':{}},write=m.write)
            report=m.evaluate(ctx,{'phase':'baseline'},path,traffic_report={})
            self.assertEqual(report['evidence']['state'],'incomplete')
            self.assertEqual(len(report['coverage']['entries']),43)
            self.assertTrue((path/'gke-evaluation.json').exists())

    def test_sum_without_explicit_monotonic_contract_cannot_pass(self):
        m=self.module();observed=self.observed(metric='system.network.io.receive',kind='sum',value=150,dimensions={'interface':'eth0','direction':'receive'})
        refs=[self.reference(metric='network.bytes',unit='bytes',value=value,dimensions={'interface':'eth0','direction':'receive'},measurement_timestamp=None,acquisition_started_at=a,acquisition_ended_at=b) for value,a,b in [(100,1,2),(200,8,9)]]
        self.assertEqual(m.compare_reference(observed,refs)['state'],'insufficient')

    def test_disk_sector_conversion_uses_pinned_512_byte_contract_and_real_read_bracket(self):
        m=self.module()
        observed=self.observed(metric='system.disk.io.read',value=1024,kind='sum',is_monotonic=True,aggregation_temporality=2,measurement_timestamp=None,source_read_bracket=[4,6],dimensions={'device':'sda','direction':'read'})
        refs=[self.reference(metric='disk.sectors',unit='sectors',value=value,measurement_timestamp=None,dimensions={'device':'sda','major':'8','minor':'0','direction':'read'},acquisition_started_at=a,acquisition_ended_at=b) for value,a,b in [(1,1,2),(3,8,9)]]
        result=m.compare_reference(observed,refs)
        self.assertEqual(result['state'],'pass',result);self.assertEqual(result['conversion_factor'],512)
        self.assertEqual(result['reference_bounds'],[512,1536])
        observed['source_read_bracket']=[1.5,6]
        self.assertEqual(m.compare_reference(observed,refs)['state'],'insufficient')

    def test_filesystem_reserved_and_percent_follow_bavail_not_bfree(self):
        m=self.module();dims={'device':'8:1','mount':'/owned'}
        refs=[self.reference(source='statvfs',metric='filesystem.'+metric,value=value,unit='bytes',dimensions=dims) for metric,value in [('capacity',100),('free',40),('available',30),('used',60)]]
        observed=self.observed(metric='system.filesystem.usage',value=10,dimensions={'device':'8:1','mountpoint':'/owned','state':'reserved'})
        result=m.compare_reference(observed,refs);self.assertEqual(result['state'],'pass',result)
        observed=self.observed(metric='system.filesystem.utilization',value=100*60/90,unit='percent',dimensions={'device':'8:1','mountpoint':'/owned'})
        self.assertEqual(m.compare_reference(observed,refs)['state'],'pass')
        refs[0]['dimensions']={'device':'8:2','mount':'/owned'}
        observed['dimensions']['device']='8:2'
        self.assertEqual(m.compare_reference(observed,refs)['state'],'insufficient')

    def test_cpu_uses_aligned_tick_intervals_and_excludes_guest_double_counting(self):
        m=self.module();refs=[]
        for timestamp in (1,5):
            for state in ('user','nice','system','idle','iowait','irq','softirq','steal','guest','guest_nice'):
                value=100 if timestamp==1 else 100+(40 if state in ('user','idle','guest') else 0)
                refs.append(self.reference(metric='cpu.time',unit='ticks',value=value,dimensions={'cpu':'cpu0','state':state},measurement_timestamp=timestamp,acquisition_started_at=timestamp-.1,acquisition_ended_at=timestamp+.1))
        observed=self.observed(metric='system.cpu.utilization',value=50,unit='percent',dimensions={'cpu':'cpu0','state':'user'},measurement_interval=[1,5])
        result=m.compare_reference(observed,refs)
        self.assertEqual(result['state'],'pass',result);self.assertEqual(result['reference_value'],50)
        observed['measurement_interval']=[2,5]
        self.assertEqual(m.compare_reference(observed,refs)['state'],'insufficient')

    def test_lifecycle_state_requires_same_pod_uid_and_timestamped_object_version(self):
        m=self.module()
        pod={'metadata':{'name':'pod','namespace':'app','uid':'pod-uid','resourceVersion':'7'},'spec':{'nodeName':'n1'},'status':{'phase':'Running','containerStatuses':[{'name':'app','containerID':'container-1','ready':True,'restartCount':2}]}}
        snapshots=[{'acquisition_started_at':4,'acquisition_ended_at':6,'node_uids':{'n1':'node-1'},'items':[pod]}]
        observed=self.observed(metric='k8s.container.ready',value=1,unit='count',source='kube-cluster',namespace='app',pod='pod',pod_uid='pod-uid',container='app',resource_version='7',dimensions={})
        self.assertEqual(m.compare_lifecycle(observed,snapshots)['state'],'pass')
        observed.update(metric='k8s.container.restarts',value=2)
        self.assertEqual(m.compare_lifecycle(observed,snapshots)['state'],'pass')
        observed.update(metric='k8s.pod.phase',value=2,container=None)
        self.assertEqual(m.compare_lifecycle(observed,snapshots)['state'],'pass')
        pod['metadata']['uid']='replacement'
        self.assertEqual(m.compare_lifecycle(observed,snapshots)['state'],'insufficient')

    def test_lifecycle_without_version_or_source_alignment_retains_blocker(self):
        m=self.module();observed=self.observed(metric='k8s.pod.phase',source='kube-cluster',value=2,unit='count',namespace='app',pod='pod',pod_uid='uid',dimensions={})
        pod={'metadata':{'uid':'uid','name':'pod','namespace':'app','resourceVersion':'7'},'spec':{'nodeName':'n1'},'status':{'phase':'Running'}}
        result=m.compare_lifecycle(observed,[{'items':[pod],'node_uids':{'n1':'node-1'},'acquisition_started_at':4,'acquisition_ended_at':6}])
        self.assertEqual(result['state'],'insufficient');self.assertTrue(result['required_acquisition'])

    def test_volume_compares_exact_receiver_measurement_to_independent_kubelet(self):
        m=self.module()
        observed=self.observed(metric='k8s.volume.available',source='kubelet',value=30,collector_uid='collector',recorded_timestamp=5,namespace='app',pod='pod',pod_uid='pod-uid',dimensions={'volume':'data','pvc_name':'claim','pvc_namespace':'app'})
        volume={'name':'data','time':'1970-01-01T00:00:04Z','availableBytes':30,'capacityBytes':100,'pvcRef':{'name':'claim','namespace':'app'}}
        body={'node':{'nodeName':'n1'},'pods':[{'podRef':{'name':'pod','namespace':'app','uid':'pod-uid'},'volume':[volume]}]}
        inputs=[{'collector_uid':'collector','node_name':'n1','status':200,'path':'/stats/summary','requested_at_ns':4900000000,'received_at_ns':5000000000,'body':body}]
        refs=[self.reference(source='kubelet-summary',scope='volume',metric='fs.availableBytes',value=30,unit='bytes',namespace='app',pod='pod',pod_uid='pod-uid',dimensions=observed['dimensions'],measurement_timestamp='1970-01-01T00:00:04Z')]
        result=m.compare_volume(observed,inputs,refs)
        self.assertEqual(result['state'],'pass',result)
        refs[0]['measurement_timestamp']='1970-01-01T00:00:03Z'
        self.assertEqual(m.compare_volume(observed,inputs,refs)['state'],'insufficient')

    def test_volume_assembly_uses_only_matching_pod_candidates_and_keeps_full_count(self):
        m = self.module()
        observed = {'metric': 'k8s.volume.available', 'pod_uid': 'pod-1', 'recorded_timestamp': 5}
        candidate = {'pod_uid': 'pod-1'}
        volume_references = [candidate, {'pod_uid': 'pod-2'}]

        def compare(observation, inputs, references, *, snapshots=(), reference_index=None):
            self.assertEqual(reference_index, {})  # Fixture rows have no source measurement timestamp.
            self.assertEqual(observation, observed)
            self.assertEqual(inputs, [])
            self.assertEqual(references, [candidate])
            return {'state': 'pass', 'reference_records_available': len(references)}

        with patch.object(m, '_volume_references', return_value=([], volume_references)), \
             patch.object(m, '_lifecycle_snapshots', return_value=[]), \
             patch.object(m, '_kubernetes_observations', return_value=[observed]), \
             patch.object(m, 'compare_volume', side_effect=compare):
            checks, inventory = m._extra_resource_checks(Path('.'), {}, [], {}, {}, 1, 9)
        self.assertEqual(checks[0]['reference_records_available'], 2)
        self.assertEqual(inventory['volume_reference_records'], 2)

    def test_traffic_accuracy_reuses_existing_parity_and_keeps_population_gate_separate(self):
        m=self.module();traffic={'independent_valid':True,'api_storage_parity':{'checks':{'exact_request_count_api':{'state':'pass'}}}}
        result=m.compare_traffic('servicegraph.request.count',traffic,{'counts_path':{'source_to_vm':{'state':'pass'}}})
        self.assertEqual(result['api_storage_parity']['state'],'pass')
        self.assertEqual(result['source_accuracy']['state'],'insufficient')
        result=m.compare_traffic('servicegraph.request.count',traffic,{'source_population':{'matched':True},'counts_path':{'source_to_vm':{'state':'pass'}}})
        self.assertEqual(result['state'],'insufficient');self.assertEqual(result['independent_comparison']['state'],'pass')

    def test_source_acquisition_attestation_can_align_read_bracket_without_replacing_recorded_time(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as d:
            path=Path(d);root=path/'agent';(root/'raw').mkdir(parents=True)
            attrs=lambda value:[{'key':key,'value':{'stringValue':val}} for key,val in value.items()]
            write=m.write
            write(root/'raw/metrics.json',json.dumps({'resourceMetrics':[{'resource':{'attributes':attrs({'k8s.node.name':'n1'})},'scopeMetrics':[{'metrics':[{'name':'system.network.io','unit':'By','sum':{'isMonotonic':True,'aggregationTemporality':2,'dataPoints':[{'asInt':'150','timeUnixNano':'5000000000','attributes':attrs({'device':'eth0','direction':'receive','karthos_source':'host-os'})}]}}]}]}]})+'\n')
            attestation={'metric':'system.network.io.receive','source':'host-os','node':'n1','node_uid':'node-1','collector_uid':'collector','raw_timestamp_unix_ns':'5000000000','source_read_bracket':[4,6],'provenance':'fixture instrumented source read'}
            write(path/'references/source-acquisition.ndjson',json.dumps(attestation)+'\n')
            observations=m._host_observations(path,{'agents':[{'root':str(root),'node_uid':'node-1','collector_uid':'collector'}]})
            self.assertIsNone(observations[0]['measurement_timestamp']);self.assertEqual(observations[0]['recorded_timestamp'],5)
            self.assertEqual(observations[0]['source_read_bracket'],[4,6])

    def test_result_assembler_wires_volume_and_gateway_lifecycle_evidence_from_real_paths(self):
        m=self.module()
        from evaluation.tests import test_gke_multinode_analysis as fixture_module
        with tempfile.TemporaryDirectory() as d:
            fixture=fixture_module.MultinodeAnalysisTests().fixture(d);path=fixture[0]
            m.write(path/'manifest.json',{'start':100,'end':110,'namespace':'app-ns'})
            m.write(path/'verification.json',{'resources':{'state':'pass'}})
            volume={'name':'data','time':90,'availableBytes':30,'capacityBytes':100,'pvcRef':{'name':'claim','namespace':'app-ns'}}
            file=path/'structured/agent/node/raw/receiver-input.ndjson';receiver=json.loads(file.read_text());receiver['body']['pods'][0]['volume']=[volume];file.write_text(json.dumps(receiver)+'\n')
            file=path/'kubelet-node.ndjson';independent=json.loads(file.read_text());independent['summary']['pods'][0]['volume']=[volume];independent.update(requested_at_ns=100400000000,received_at_ns=100500000000);file.write_text(json.dumps(independent)+'\n')
            attrs=lambda values:[{'key':key,'value':{'stringValue':value}} for key,value in values.items()]
            resource={'k8s.node.name':'node','k8s.pod.uid':'pod-uid','k8s.pod.name':'app-pod','k8s.namespace.name':'app-ns','k8s.volume.name':'data','k8s.volume.type':'persistentVolumeClaim','k8s.persistentvolumeclaim.name':'claim'}
            metric={'name':'k8s.volume.available','unit':'By','gauge':{'dataPoints':[{'asInt':'30','timeUnixNano':'100100000000','attributes':attrs({'karthos_source':'kubelet'})}]}}
            with (path/'structured/agent/node/raw/metrics.json').open('a') as handle:handle.write(json.dumps({'resourceMetrics':[{'resource':{'attributes':attrs(resource)},'scopeMetrics':[{'metrics':[metric]}]}]})+'\n')
            resource.pop('k8s.volume.name');resource.pop('k8s.volume.type');resource.pop('k8s.persistentvolumeclaim.name');resource['k8s.pod.resource_version']='7'
            metric={'name':'k8s.pod.phase','unit':'','gauge':{'dataPoints':[{'asInt':'2','timeUnixNano':'100100000000','attributes':attrs({'karthos_source':'kube-cluster'})}]}}
            m.write(path/'structured/gateway/pod.json',{'metadata':{'uid':'gateway-uid'}})
            m.write(path/'structured/gateway/raw/metrics.json',json.dumps({'resourceMetrics':[{'resource':{'attributes':attrs(resource)},'scopeMetrics':[{'metrics':[metric]}]}]})+'\n')
            pod={'metadata':{'uid':'pod-uid','name':'app-pod','namespace':'app-ns','resourceVersion':'7'},'spec':{'nodeName':'node'},'status':{'phase':'Running'}}
            m.write(path/'extended/kubernetes-inventory.json',{'snapshots':[{'node_uids':{'node':'node-uid-node'},'items':[pod],'acquisition_started_at':100.4,'acquisition_ended_at':100.5}]})
            ctx=SimpleNamespace(root=ROOT,manifest={'thresholds':{},'config':{}},write=m.write)
            report=m.evaluate(ctx,{'phase':'baseline'},path,traffic_report={'independent_valid':True,'execution':{'state':'complete'}})
            selected={row['name']:row for row in report['selected_accuracy']}
            self.assertEqual(selected['k8s.volume.available']['state'],'pass',selected['k8s.volume.available'])
            self.assertEqual(selected['k8s.pod.phase']['state'],'pass',selected['k8s.pod.phase'])
            self.assertEqual(report['reference_inventory']['volume_reference_records'],2)

    def test_extended_queries_use_deployed_source_and_capture_selected_query_parity(self):
        m=self.module();calls=[]
        def pages(base,route,params,key,pages_dir=None):calls.append((route,dict(params)));return {'data':{key:[]}}
        def inventory(*args):
            if 'services' in args:return {'items':[{'metadata':{'name':'victoria'}}]}
            if 'configmap' in args:return {'data':{'source-plan.json':json.dumps({'metric_assignments':{'system.memory.usage':'host-os'}})}}
            return {'items':[]}
        with tempfile.TemporaryDirectory() as d:
            ctx=SimpleNamespace(root=ROOT,manifest={'config':{}},json=inventory,write=m.write)
            with patch('evaluation.gke.parity.capture',return_value={'metrics':[]}) as parity,patch.object(m.r,'forward',side_effect=lambda *a:contextlib.nullcontext('http://fixture')),patch.object(m.r,'api_pages',side_effect=pages),patch.object(m.r,'get_json',return_value={}),patch.object(m.r.urllib.request,'urlopen',return_value=contextlib.nullcontext(SimpleNamespace(read=lambda *a:b''))):
                m.capture_extended(ctx,SimpleNamespace(namespace='app',source='trace'),Path(d),0,10)
            memory=[params for route,params in calls if route=='/api/v1/metrics' and params.get('metric_name')=='system.memory.usage']
            self.assertEqual(memory[0]['source'],'host-os')
            self.assertEqual(len(parity.call_args.args[3]),18)
            self.assertEqual(parity.call_args.args[4]['system.memory.usage'],'host-os')
            self.assertTrue((Path(d)/'extended/parity/manifest.json').exists())

    def test_native_nonmonotonic_cumulative_memory_sum_is_a_level_not_a_rate(self):
        m=self.module()
        observed=self.observed(kind='sum',is_monotonic=False,aggregation_temporality=2)
        self.assertEqual(m.compare_reference(observed,[self.reference()])['state'],'pass')

    def test_delta_monotonic_sum_cannot_be_compared_to_cumulative_proc_bytes(self):
        m=self.module();observed=self.observed(metric='system.network.io.receive',kind='sum',is_monotonic=True,aggregation_temporality=1,value=150,dimensions={'interface':'eth0','direction':'receive'})
        refs=[self.reference(metric='network.bytes',unit='bytes',value=value,dimensions={'interface':'eth0','direction':'receive'},measurement_timestamp=None,acquisition_started_at=a,acquisition_ended_at=b) for value,a,b in [(100,1,2),(200,8,9)]]
        self.assertEqual(m.compare_reference(observed,refs)['state'],'insufficient')
