import copy
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
try:
    from evaluation.gke.consumer import capture_consumer
except ImportError:
    capture_consumer=None
from evaluation.gke.models import write
from evaluation.gke.diagnostics import resources
IMAGE='registry.invalid/helper@sha256:'+'1'*64

class ConsumerJobTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(capture_consumer,'API-only Kubernetes job integration missing')
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.window=self.root/'window';self.window.mkdir()
        self.source=Path(__file__).resolve().parents[2]
        (self.root/'evaluation/consumers').mkdir(parents=True)
        shutil.copyfile(self.source/'evaluation/consumers/api_only.py',self.root/'evaluation/consumers/api_only.py')
        (self.root/'protocols/gke-qualification-v1').mkdir(parents=True)
        write(self.root/'protocols/gke-qualification-v1/queries.yaml',{'api_only':True,'questions':['callers-of-checkout','before-after-observed-snapshots'],'missing_is_unknown':True})
        write(self.root/'protocols/gke-qualification-v1/metrics.yaml',{'metrics':[{'name':'servicegraph.request.count'},{'name':'system.memory.usage'}]})
        write(self.window/'manifest.json',{'start':'2026-10-31T12:00:00Z','end':'2026-10-31T12:05:00Z','source_id':'mesh.istio'})
        self.objects={};self.calls=[];self.foreign=False
        def kube(*args,**kw):
            self.calls.append((args,kw));out=''
            if args[0]=='get':
                kind,name=args[1:3]
                if kind=='service':
                    if name=='karthos-backend': out=json.dumps({'spec':{'clusterIP':'10.0.0.1','selector':{'component':'backend'},'ports':[{'name':'api','port':8080,'targetPort':'api'}]}})
                    else: out=json.dumps({'spec':{'clusterIP':'10.0.0.10'}})
                else:
                    obj=self.objects.get((kind,name))
                    if kind=='namespace' and self.foreign: obj={'metadata':{'uid':'foreign','labels':{}}}
                    if obj:out=json.dumps(obj)
            elif args[0]=='create':
                obj=json.loads(kw['input_text']);obj=copy.deepcopy(obj);obj['metadata']['uid']='uid-'+obj['kind'].lower()
                self.objects[(obj['kind'].lower(),obj['metadata']['name'])]=obj;out=json.dumps(obj)
            elif args[0]=='logs':
                graph={'state':'observed','snapshot':{'id':1,'created_at':'2026-10-31T12:00:00Z'},'nodes':[],'edges':[],'requests':[]}
                out=json.dumps({'before':graph,'after':graph,'snapshot_changed':False,'metrics':[],'answers':{'callers':{'state':'insufficient'}}})
            elif args[:2]==('delete','--raw'):
                options=json.loads(kw['input_text']);self.assertIn('uid',options['preconditions'])
            return SimpleNamespace(returncode=0,stdout=out,stderr='')
        self.ctx=SimpleNamespace(root=self.root,run_id='run',config={'readiness':{'resource_helper_image':IMAGE}},kube=kube,write=write)
    def test_job_has_only_public_api_code_no_tokens_and_restricted_egress(self):
        result=capture_consumer(self.ctx,self.window,'baseline')
        created=[json.loads(kw['input_text']) for args,kw in self.calls if args[0]=='create']
        job=next(o for o in created if o['kind']=='Job');spec=job['spec']['template']['spec']
        self.assertFalse(spec['automountServiceAccountToken']);self.assertNotIn('hostNetwork',spec)
        self.assertEqual(spec['nodeSelector']['evaluation.karthos.dev/slot'],'observability')
        self.assertEqual(spec['containers'][0]['image'],IMAGE)
        self.assertTrue(all('configMap' in v for v in spec['volumes']))
        policy=next(o for o in created if o['kind']=='NetworkPolicy')
        self.assertEqual(policy['spec']['policyTypes'],['Ingress','Egress'])
        self.assertEqual(policy['spec']['ingress'],[])
        self.assertTrue(all(rule.get('to') for rule in policy['spec']['egress']))
        self.assertLess(next(i for i,o in enumerate(created) if o['kind']=='NetworkPolicy'),next(i for i,o in enumerate(created) if o['kind']=='Job'))
        self.assertEqual(result['state'],'observed')
        for name in ('ledger.json','questions.json','snapshot-ids.json','stdout.json','status.json'): self.assertTrue((self.window/'consumer'/name).exists(),name)
        self.assertTrue(any(args[:2]==('delete','--raw') for args,kw in self.calls))
    def test_consumer_emits_large_ledger_as_bounded_json_lines(self):
        import contextlib
        import io
        from unittest.mock import patch
        from evaluation.gke.consumer import JOB_SCRIPT

        samples = [{'value': index, 'labels': {'source': 'fixture'}} for index in range(80)]
        observation = {'before': {'state': 'observed'}, 'after': {'state': 'observed'},
                       'metrics': [{'samples': samples}]}
        client = SimpleNamespace(observe=lambda *args, **kwargs: observation)
        module = SimpleNamespace(APIConsumer=lambda *args, **kwargs: client,
                                 answer_questions=lambda *args, **kwargs: {'resource_traffic': samples})
        spec = SimpleNamespace(loader=SimpleNamespace(exec_module=lambda _: None))
        config = {'endpoint': 'http://example.invalid', 'before': 'before', 'after': 'after',
                  'queries': [], 'questions': [], 'service': 'checkout'}
        output = io.StringIO()
        with patch('importlib.util.spec_from_file_location', return_value=spec), \
             patch('importlib.util.module_from_spec', return_value=module), \
             patch.object(Path, 'read_text', return_value=json.dumps(config)), \
             contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as exited:
            exec(compile(JOB_SCRIPT, 'job.py', 'exec'), {})
        self.assertEqual(exited.exception.code, 0)
        self.assertEqual(json.loads(output.getvalue()), observation)
        self.assertGreater(len(output.getvalue()), 1024)
        self.assertLess(max(map(len, output.getvalue().splitlines())), 128)

    def test_foreign_namespace_is_never_adopted_or_deleted(self):
        self.foreign=True
        with self.assertRaises(ValueError): capture_consumer(self.ctx,self.window,'baseline')
        self.assertFalse(any(args[0] in ('create','delete') for args,kw in self.calls))
    def test_missing_bounds_are_insufficient_without_kube_calls(self):
        write(self.window/'manifest.json',{'source_id':'mesh.istio'})
        result=capture_consumer(self.ctx,self.window,'baseline')
        self.assertEqual(result['state'],'insufficient');self.assertEqual(self.calls,[])
    def test_invalid_image_is_rejected_before_kube_calls(self):
        self.ctx.config['readiness']['resource_helper_image']='mutable:latest'
        with self.assertRaises(ValueError): capture_consumer(self.ctx,self.window,'baseline')
        self.assertEqual(self.calls,[])
    def test_both_application_workers_receive_independent_cpu_and_storage_stimulus(self):
        nodes=[{'name':'infra','slot':'infra','uid':'0'},{'name':'a','slot':'app-a','uid':'1'},{'name':'b','slot':'app-b','uid':'2'}]
        for scenario in ('cpu-memory','storage'):
            objects=resources.build_resources_both(scenario,'eval',nodes,IMAGE,'run')
            pods=[o for o in objects if o['kind']=='Pod']
            self.assertEqual({p['spec']['nodeSelector']['kubernetes.io/hostname'] for p in pods},{'a','b'})
            names=[(o['kind'],o['metadata']['name']) for o in objects];self.assertEqual(len(names),len(set(names)))
            self.assertTrue(all(o['metadata']['labels']['evaluation.karthos.dev/run-id']=='run' for o in objects))
            if scenario=='storage':
                self.assertEqual(len([o for o in objects if o['kind']=='PersistentVolumeClaim']),2)
                uids=[next(e['value'] for e in p['spec']['containers'][1]['env'] if e['name']=='NODE_UID') for p in pods]
                self.assertEqual(set(uids),{'1','2'})
    def test_cleanup_refuses_replaced_job_and_retains_namespace_for_inspection(self):
        original=self.ctx.kube
        replaced=False
        def kube(*args,**kwargs):
            nonlocal replaced
            if args[0]=='logs' and not replaced:
                job=next(obj for (kind,name),obj in self.objects.items() if kind=='job')
                job['metadata']['uid']='replacement';replaced=True
            return original(*args,**kwargs)
        self.ctx.kube=kube
        status=capture_consumer(self.ctx,self.window,'baseline')
        self.assertEqual(status['state'],'insufficient');self.assertTrue(status['cleanup_errors'])
        deletes=[args[2] for args,kwargs in self.calls if args[:2]==('delete','--raw')]
        self.assertFalse(any(uri.startswith('/api/v1/namespaces/api-consumer-') and uri.count('/')==4 for uri in deletes))
    def test_configmap_embeds_executable_public_consumer_and_exact_source_filters(self):
        capture_consumer(self.ctx,self.window,'resource')
        cm=next(o for (kind,name),o in self.objects.items() if kind=='configmap')
        compile(cm['data']['job.py'],'job.py','exec')
        self.assertEqual(cm['data']['api_only.py'],(self.source/'evaluation/consumers/api_only.py').read_text())
        query=json.loads(cm['data']['queries.json'])
        traffic=next(q for q in query['queries'] if q['metric_name']=='servicegraph.request.count')
        resource=next(q for q in query['queries'] if q['metric_name']=='system.memory.usage')
        self.assertEqual(traffic['source'],'mesh.istio');self.assertNotIn('source',resource)
        self.assertEqual(query['before'],'2026-10-31T12:00:00Z')
        self.assertEqual(query['after'],'2026-10-31T12:05:00Z')

    def test_thesis_job_requires_its_owned_completion_and_observed_namespace_removal(self):
        import time
        from unittest.mock import patch
        from evaluation.gke.models import THESIS

        definitions=self.root/'protocols'/THESIS
        write(definitions/'queries.yaml',{'api_only':True,'missing_is_unknown':True,'schema_version':2,
            'services':{'boutique':'checkoutservice'},'containers':{'boutique':'server'},'questions':['fixture']})
        write(definitions/'metrics.yaml',{'metrics':[]})
        start='2026-10-31T12:00:00Z';end='2026-10-31T12:05:00Z'
        write(self.window/'manifest.json',{'start':start,'end':end,'namespace':'demo','source_id':'mesh.istio'})
        self.ctx.manifest={'schedule':THESIS,'product_pins':{'openapi_sha256':'fixture'}}
        original=self.ctx.kube

        def kube(*args,**kwargs):
            response=original(*args,**kwargs)
            if args[0]=='wait':
                self.objects['job','api-consumer']['status']={'conditions':[{'type':'Complete','status':'True'}]}
            elif args[0]=='logs':
                snapshot=lambda at,index:{'state':'observed','at':at,'snapshot':{'id':index,'created_at':at}}
                response.stdout=json.dumps({'state':'observed','openapi_validated':True,
                    'requested_questions':['fixture'],'before':snapshot(start,1),'after':snapshot(end,2)})
            elif args[:2]==('delete','--raw') and args[2].count('/')==4:
                self.objects.pop(('namespace',args[2].rsplit('/',1)[-1]))
            return response

        self.ctx.kube=kube
        with patch('evaluation.gke.thesis.openapi_projection',return_value={'fixture':True}):
            result=capture_consumer(self.ctx,self.window,'history',spec={'demo':'boutique'},
                                    timeout=80,deadline=time.monotonic()+160)
        self.assertTrue(result['valid'])
        self.assertTrue(result['job_completed'])
        self.assertTrue(result['cleanup_verified'])
        namespace_delete=max(i for i,(args,_) in enumerate(self.calls) if args[:2]==('delete','--raw'))
        self.assertEqual(self.calls[namespace_delete+1][0][:2],('get','namespace'))
        job=self.objects['job','api-consumer']
        self.assertLess(job['spec']['activeDeadlineSeconds'],160)
