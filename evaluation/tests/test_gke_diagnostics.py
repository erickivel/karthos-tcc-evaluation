import copy
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
try:
    from evaluation.gke.diagnostics import resources, arbitration, restarts
except ImportError:
    resources=arbitration=restarts=None
IMAGE='registry.test/helper@sha256:'+'1'*64
NODES=[{'name':'infra','slot':'infra','uid':'u0'},{'name':'a','slot':'app-a','uid':'u1'},{'name':'b','slot':'app-b','uid':'u2'}]
OWNER={'evaluation.karthos.dev/owner':'gke-qualification-v1','evaluation.karthos.dev/run-id':'run'}

class DiagnosticsTests(unittest.TestCase):
    def setUp(self): self.assertIsNotNone(resources,'diagnostic modules missing')
    def test_resources_bounded_pinned_and_owned(self):
        for scenario in ('cpu-memory','storage','network'):
            objects=resources.build_resources(scenario,'eval',NODES,IMAGE,'run')
            self.assertTrue(objects)
            for obj in objects:
                self.assertEqual(obj['metadata']['labels']['evaluation.karthos.dev/run-id'],'run')
                if obj['kind']=='Pod':
                    spec=obj['spec'];self.assertFalse(spec['automountServiceAccountToken'])
                    self.assertLessEqual(spec['activeDeadlineSeconds'],360)
                    self.assertIn(spec['nodeSelector']['kubernetes.io/hostname'],('a','b'))
                    for c in spec['containers']: self.assertEqual(c['image'],IMAGE)
        cpu=resources.build_resources('cpu-memory','eval',NODES,IMAGE,'run')[0]
        self.assertIn('stress-ng',cpu['spec']['containers'][0]['command'])
        self.assertEqual(cpu['spec']['containers'][0]['workingDir'],'/tmp')
        self.assertIn('256M',cpu['spec']['containers'][0]['args'])
        storage=resources.build_resources('storage','eval',NODES,IMAGE,'run')
        sc=next(x for x in storage if x['kind']=='StorageClass')
        self.assertEqual(sc['provisioner'],'pd.csi.storage.gke.io')
        self.assertIn('evaluation-run=run',sc['parameters']['labels'])
        self.assertEqual(sc['reclaimPolicy'],'Delete')
    def test_negative_manifest_inputs(self):
        for scenario,nodes,image in [('unknown',NODES,IMAGE),('storage',NODES,'mutable:latest'),('network',NODES[:2],IMAGE),('cpu-memory',[NODES[0],NODES[1],NODES[1]],IMAGE)]:
            with self.assertRaises(ValueError): resources.build_resources(scenario,'eval',nodes,image,'run')
        with self.assertRaises(ValueError): resources.build_resources('cpu-memory','eval',NODES,IMAGE,'run',duration_seconds=3600)
    def test_reference_daemonset_read_only_and_no_token(self):
        ds=resources.build_reference_daemonset('eval',IMAGE,'run',nodes=NODES)
        spec=ds['spec']['template']['spec']
        self.assertTrue(spec['hostNetwork']);self.assertFalse(spec['automountServiceAccountToken'])
        self.assertTrue(spec['containers'][0]['volumeMounts'][0]['readOnly'])
        env={e['name']:e for e in spec['containers'][0]['env']}
        self.assertEqual(json.loads(env['NODE_UID_MAP']['value'])['a'],'u1')
        self.assertEqual(env['POD_UID']['valueFrom']['fieldRef']['fieldPath'],'metadata.uid')
    def test_reference_node_field_selectors_accept_exactly_one_name_per_or_term(self):
        ds=resources.build_reference_daemonset('eval',IMAGE,'run',nodes=NODES)
        terms=ds['spec']['template']['spec']['affinity']['nodeAffinity']['requiredDuringSchedulingIgnoredDuringExecution']['nodeSelectorTerms']
        selected=[]
        for term in terms:
            fields=term['matchFields']
            self.assertEqual(len(fields),1)
            field=fields[0]
            self.assertEqual(field['key'],'metadata.name')
            self.assertEqual(field['operator'],'In')
            self.assertEqual(len(field['values']),1,'Kubernetes field selectors require one value per term')
            selected.extend(field['values'])
        self.assertCountEqual(selected,[node['name'] for node in NODES])
        self.assertEqual(len(terms),len(NODES))

    def bundle(self):
        profile={'capabilities':[{'source_id':'mesh.istio','status':'available'},{'source_id':'traces.servicegraph','status':'available'}]}
        resolved={'profile_fingerprint':'captured-fingerprint','selected_sources':['mesh.istio'],'metric_assignments':{'servicegraph.request.count':'mesh.istio'}}
        plan={'source_plan':resolved,'backend_source_contract':{'capabilityProfile':copy.deepcopy(profile),'sourcePlan':copy.deepcopy(resolved)},'collector_configs':[{'connectors':{},'service':{'pipelines':{}}}]}
        samples=[{'metric_name':'servicegraph.request.count','labels':{'source':'mesh.istio'},'value':1}]
        return profile,plan,samples
    def test_arbitration_checks_plan_config_and_samples(self):
        profile,plan,samples=self.bundle()
        self.assertEqual(arbitration.assert_selection(profile,plan,samples,'mesh.istio')['state'],'pass')
        self.assertEqual(arbitration.assert_selection(profile,plan,[],'mesh.istio')['state'],'insufficient')
        for mutator in (lambda p:p['backend_source_contract']['sourcePlan'].update(selected_sources=[]), lambda p:p['collector_configs'][0].update(connectors={'servicegraph':{}})):
            invalid=copy.deepcopy(plan);mutator(invalid)
            self.assertEqual(arbitration.assert_selection(profile,invalid,samples,'mesh.istio')['state'],'fail')
        self.assertEqual(arbitration.assert_selection(profile,plan,[dict(samples[0],labels={'source':'traces.servicegraph'})],'mesh.istio')['state'],'fail')
    def test_transition_is_explicit_rediscovery_before_redeployment(self):
        calls=[]
        result=arbitration.transition_to_trace(make_istio_unavailable=lambda:calls.append('unavailable'),rediscover=lambda:(calls.append('discover') or {'new':'plan'}),redeploy=lambda p:calls.append(('deploy',p)))
        self.assertEqual(calls,['unavailable','discover',('deploy',{'new':'plan'})])
        self.assertEqual(result['mode'],'rediscover-redeploy')
    def test_restart_owned_once_and_preconditioned(self):
        calls=[];records={}
        obj={'metadata':{'name':'checkout','uid':'uid','resourceVersion':'12','labels':dict(OWNER)},'spec':{'template':{'metadata':{'annotations':{}}}}}
        def kube(*args,**kwargs):
            calls.append((args,kwargs));return SimpleNamespace(returncode=0,stdout=json.dumps(obj),stderr='')
        ctx=SimpleNamespace(kube=kube,write=lambda path,data:records.update({str(path):data}),clock=lambda:123)
        event=restarts.restart_target(ctx,'eval',{'kind':'deployment','name':'checkout'},'run')
        self.assertEqual(event['state'],'requested');self.assertEqual(event['requested_at'],123)
        patch=json.loads(calls[-1][0][calls[-1][0].index('-p')+1])
        self.assertEqual(patch[0],{'op':'test','path':'/metadata/uid','value':'uid'})
        self.assertEqual(sum('patch' in args for args,kwargs in calls),1)
        with self.assertRaises(ValueError): restarts.restart_target(ctx,'eval',{'kind':'deployment','name':'checkout'},'run')
        self.assertTrue(records)
    def test_planned_gateway_restart_stays_inside_the_guarded_restore_plan(self):
        from evaluation.capture_watchdog import OWNER as capture_owner, restore_patch

        token = 'w-' + 'a' * 18
        restart_key = 'evaluation.karthos.dev/restart-window'
        original = {
            'metadata': {'name': 'karthos-gateway', 'uid': 'uid', 'resourceVersion': '12', 'labels': dict(OWNER)},
            'spec': {'template': {'metadata': {'annotations': {}}, 'spec': {'containers': [{'name': 'gateway'}]}}},
        }
        instrumented = copy.deepcopy(original)
        instrumented['spec']['template']['metadata']['annotations'][capture_owner] = 'capture'
        plan = {'kind': 'gateway', 'owner': 'capture', 'gateway_restart_token': token,
                'before_workload': original, 'after_workload': instrumented}
        current = copy.deepcopy(instrumented)
        mutations = []

        def kube(action, *args, **kwargs):
            if action == 'get':
                return SimpleNamespace(returncode=0, stdout=json.dumps(current))
            patch = json.loads(args[args.index('-p') + 1])
            mutations.append(patch)
            current['spec']['template']['metadata']['annotations'] = patch[-1]['value']
            return SimpleNamespace(returncode=0, stderr='')

        ctx = SimpleNamespace(kube=kube, write=lambda *_: None, clock=lambda: 123)
        event = restarts.restart_target(ctx, 'eval', 'gateway', 'run', capture_plan=plan)
        self.assertEqual(event['requested_at'], 123)
        self.assertEqual(len(mutations), 1)
        annotations = current['spec']['template']['metadata']['annotations']
        self.assertEqual(annotations, {capture_owner: 'capture', restart_key: token})
        patch = restore_patch(plan, 'workload', current)
        self.assertEqual(patch[-2]['value'], current['spec']['template'])
        self.assertEqual(patch[-1]['value'], original['spec']['template'])

        for name, value in ((restart_key, 'foreign'), ('user-edit', 'preserve')):
            altered = copy.deepcopy(current)
            altered['spec']['template']['metadata']['annotations'][name] = value
            with self.assertRaises(ValueError):
                restore_patch(plan, 'workload', altered)
        legacy_plan = {key: value for key, value in plan.items() if key != 'gateway_restart_token'}
        with self.assertRaises(ValueError):
            restore_patch(legacy_plan, 'workload', current)

    def test_restart_observation_requires_new_generation_and_ready_pod_uid(self):
        from unittest.mock import patch

        def deployment(generation, observed, uid='deployment'):
            return {'metadata': {'uid': uid, 'generation': generation, 'labels': dict(OWNER)},
                    'spec': {'replicas': 1}, 'status': {'observedGeneration': observed}}

        def pod(uid, ready=True):
            return {'metadata': {'uid': uid, 'labels': dict(OWNER)}, 'status': {
                'phase': 'Running', 'conditions': [{'type': 'Ready', 'status': 'True' if ready else 'False'}]}}

        before = deployment(3, 3)
        original = {'items': [pod('old')]}
        replacements = iter([
            before, original,
            deployment(4, 3), {'items': [pod('new', False)]},
            deployment(4, 4), {'items': [pod('new')]},
        ])
        def kube(*args, **kwargs):
            self.assertGreater(kwargs['timeout'], 0)
            return SimpleNamespace(stdout=json.dumps(next(replacements)))
        with patch('evaluation.gke.diagnostics.restarts.time.sleep'):
            after, pods = restarts.observe_restart(kube, 'eval', 'checkoutservice', 'app=checkout',
                                                   'run', before, original, timeout=5)
        self.assertEqual(after['metadata']['generation'], 4)
        self.assertEqual(pods['items'][0]['metadata']['uid'], 'new')

        def foreign(*args, **kwargs):
            return SimpleNamespace(stdout=json.dumps(deployment(4, 4, uid='foreign')))
        with self.assertRaisesRegex(ValueError, 'deployment identity'):
            restarts.observe_restart(foreign, 'eval', 'checkoutservice', 'app=checkout',
                                     'run', before, original, timeout=5)

    def test_restart_rejects_foreign_ownership_without_patch(self):
        calls=[]
        def kube(*args,**kwargs):
            calls.append(args);return SimpleNamespace(returncode=0,stdout=json.dumps({'metadata':{'uid':'u','resourceVersion':'1','labels':{}}}),stderr='')
        ctx=SimpleNamespace(kube=kube,write=lambda *a:None)
        with self.assertRaises(ValueError): restarts.restart_target(ctx,'eval','checkout','run')
        self.assertEqual(len(calls),1)
    def test_reference_sampler_has_no_credentials_and_preserves_node_identity(self):
        path=Path(__file__).resolve().parents[2]/'infrastructure/helpers/reference_sampler.py'
        self.assertTrue(path.exists(),'reference sampler missing')
        spec=importlib.util.spec_from_file_location('gke_reference_sampler',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        def read(path): return 'cpu 1 2 3 4' if str(path).endswith('/proc/stat') else 'boot' if str(path).endswith('boot_id') else ''
        rows=module.sample_once(node='a',node_uid='u1',pod_uid='sampler-pod',run_id='run',read_text=read,clock=iter([1,2]).__next__)
        self.assertTrue(rows);self.assertEqual(rows[0]['node_uid'],'u1');self.assertEqual(rows[0]['observer_pod_uid'],'sampler-pod')
        self.assertEqual(rows[0]['acquisition_started_at'],1)
    def test_trace_connector_must_be_active_and_unique(self):
        profile,plan,samples=self.bundle()
        resolved=plan['source_plan'];resolved['selected_sources']=['traces.servicegraph'];resolved['metric_assignments']={'servicegraph.request.count':'traces.servicegraph'}
        plan['backend_source_contract']['sourcePlan']=copy.deepcopy(resolved)
        plan['collector_configs']=[{'connectors':{'servicegraph':{}},'service':{'pipelines':{'traces':{'exporters':['servicegraph']},'metrics':{'receivers':['servicegraph']}}}}]
        samples[0]['labels']['source']='traces.servicegraph'
        self.assertEqual(arbitration.assert_selection(profile,plan,samples,'traces.servicegraph')['state'],'pass')
        plan['collector_configs'].append(copy.deepcopy(plan['collector_configs'][0]))
        self.assertEqual(arbitration.assert_selection(profile,plan,samples,'traces.servicegraph')['state'],'fail')
    def test_deployed_service_graph_component_id_is_recognized(self):
        profile, plan, samples = self.bundle()
        connector = 'service_graph/traffic'
        config = {'connectors': {connector: {}}, 'service': {'pipelines': {
            'traces': {'exporters': [connector]}, 'metrics': {'receivers': [connector]}}}}
        plan['collector_configs'] = [config]
        self.assertEqual(arbitration.assert_selection(profile, plan, samples, 'mesh.istio')['state'], 'fail')
        source = 'traces.servicegraph'
        plan['source_plan'].update(selected_sources=[source], metric_assignments={'servicegraph.request.count': source})
        plan['backend_source_contract']['sourcePlan'] = copy.deepcopy(plan['source_plan'])
        samples[0]['labels']['source'] = source
        self.assertEqual(arbitration.assert_selection(profile, plan, samples, source)['state'], 'pass')
        config['service']['pipelines']['metrics']['receivers'] = ['service_graph/other']
        self.assertEqual(arbitration.assert_selection(profile, plan, samples, source)['state'], 'fail')

    def test_transition_failure_does_not_redeploy(self):
        calls=[]
        def discover():
            calls.append('discover');raise RuntimeError('discovery failed')
        with self.assertRaises(RuntimeError):
            arbitration.transition_to_trace(make_istio_unavailable=lambda:calls.append('unavailable'),rediscover=discover,redeploy=lambda _:calls.append('deploy'))
        self.assertEqual(calls,['unavailable','discover'])
    def test_failed_restart_is_not_automatically_retried(self):
        calls=[];records={}
        obj={'metadata':{'uid':'uid','resourceVersion':'12','labels':dict(OWNER)},'spec':{'template':{'metadata':{}}}}
        def kube(*args,**kwargs):
            calls.append(args)
            return SimpleNamespace(returncode=1 if 'patch' in args else 0,stdout=json.dumps(obj),stderr='conflict')
        ctx=SimpleNamespace(kube=kube,write=lambda path,data:records.update({str(path):data}))
        with self.assertRaises(RuntimeError): restarts.restart_target(ctx,'eval',{'kind':'deployment','name':'checkout'},'run')
        with self.assertRaises(ValueError): restarts.restart_target(ctx,'eval',{'kind':'deployment','name':'checkout'},'run')
        self.assertEqual(sum('patch' in args for args in calls),1)
        self.assertEqual(next(iter(records.values()))['state'],'failed')
    def test_owned_volume_sampler_uses_actual_device_and_read_only_statvfs(self):
        path=Path(__file__).resolve().parents[2]/'infrastructure/helpers/reference_sampler.py'
        spec=importlib.util.spec_from_file_location('gke_reference_sampler',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        seen=[]
        def stat(path): seen.append(path);return SimpleNamespace(st_dev=os.makedev(8,0))
        def statvfs(path): return SimpleNamespace(f_blocks=10,f_bfree=4,f_bavail=3,f_frsize=4096)
        rows=module.sample_once(node='a',node_uid='u',pod_uid='p',run_id='run',volume_only=True,owned_mounts=[{'mount':'/owned','owned':True}],stat=stat,statvfs=statvfs,clock=iter([1,2]).__next__)
        self.assertEqual(seen,['/owned']);self.assertEqual(rows[0]['dimensions']['device'],'8:0')
        self.assertEqual(rows[0]['scope'],'owned-volume')
        with self.assertRaises(ValueError): module.sample_once(node='a',node_uid='u',pod_uid='p',run_id='run',volume_only=True,owned_mounts=[{'mount':'/owned','owned':False}],stat=stat,statvfs=statvfs)

class NetworkFixtureTests(unittest.TestCase):
    def test_network_probe_never_consumes_the_one_off_session(self):
        from unittest.mock import patch
        import subprocess, sys
        server = resources.build_resources('network','eval',NODES,IMAGE,'run')[0]
        probe = server['spec']['containers'][0]['readinessProbe']
        self.assertNotIn('tcpSocket',probe)
        command = probe['exec']['command']
        self.assertEqual(command[0:2],['python3','-c'])
        listener='  sl  local_address rem_address st\n   0: 00000000:1451 00000000:0000 0A\n'
        connected=listener.replace('0A','01')
        for table,expected in [(listener,0),(connected,1)]:
            script='from unittest.mock import mock_open, patch\nwith patch("builtins.open", mock_open(read_data='+repr(table)+')):\n '+command[2].replace('\n','\n ')
            result=subprocess.run([sys.executable,'-c',script],capture_output=True)
            self.assertEqual(result.returncode,expected,result.stderr)

    def test_network_client_targets_exact_ready_owned_server_pod(self):
        server, client = resources.build_resources('network', 'eval', NODES, IMAGE, 'run')
        pod = copy.deepcopy(server)
        pod['metadata']['uid'] = 'server-uid'
        pod['spec']['nodeName'] = 'b'
        pod['status'] = {'phase': 'Running', 'podIP': '10.44.2.91',
                         'conditions': [{'type': 'Ready', 'status': 'True'}]}
        for changes in ({'spec': {'nodeName': 'a'}},
                        {'metadata': {'name': pod['metadata']['name'], 'uid': 'server-uid'}},
                        {'status': {'phase': 'Running', 'podIP': '10.44.2.91'}}):
            with self.assertRaises(ValueError):
                resources.bind_network_target(server, copy.deepcopy(client), dict(pod, **changes))
        target = resources.bind_network_target(server, client, pod)
        self.assertEqual(target['pod_uid'], 'server-uid')
        self.assertEqual(client['spec']['containers'][0]['args'][:2], ['--client', '10.44.2.91'])
        self.assertIn('300', client['spec']['containers'][0]['args'])
        self.assertIn('10M', client['spec']['containers'][0]['args'])

    def test_iperf_error_json_is_invalid_even_if_the_pod_succeeded(self):
        for payload in ({'error':'unable to connect','end':{}},{'end':{'sum_received':{'bytes':0}}}):
            with self.assertRaises(ValueError):
                resources.verify_network_result(json.dumps(payload))
        resources.verify_network_result('{"end":{"sum_received":{"bytes":1310720}}}')
