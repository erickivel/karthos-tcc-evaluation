import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('watchdog', ROOT / 'infrastructure/watchdog/main.py')

class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SPEC.origin and Path(SPEC.origin).exists(), 'watchdog implementation missing')
        self.mod = importlib.util.module_from_spec(SPEC)
        SPEC.loader.exec_module(self.mod)
        self.record = {'project_id':'research-project', 'owner_id':'research', 'run_id':'run-1', 'started_at':100, 'deadline':43300,
                       'resources':[{'kind':'instance', 'name':'projects/research-project/zones/us-central1-a/instances/controller', 'uid':'123'}]}
        self.cloud = FakeCloud()
    def test_dry_run_never_deletes(self):
        result = self.mod.enforce(self.record, self.cloud, now=43300, dry_run=True)
        self.assertEqual(len(result['eligible']), 1)
        self.assertEqual(self.cloud.deleted, [])
    def test_expired_owned_uid_deleted(self):
        self.mod.enforce(self.record, self.cloud, now=43300, dry_run=False)
        self.assertEqual(len(self.cloud.deleted), 1)
    def test_before_deadline_never_deletes(self):
        self.mod.enforce(self.record, self.cloud, now=43299, dry_run=False)
        self.assertEqual(self.cloud.deleted, [])
    def test_wrong_uid_or_ownership_never_deleted(self):
        for key, value in [('uid','recreated'), ('owner_id','someone-else'), ('run_id','run-2')]:
            self.cloud.resource[key] = value
            result = self.mod.enforce(self.record, self.cloud, now=43301, dry_run=False)
            self.assertEqual(self.cloud.deleted, [])
            self.assertTrue(result['refused'])
    def test_excessive_deadline_rejected(self):
        self.record['deadline'] += 1
        with self.assertRaises(ValueError):
            self.mod.enforce(self.record, self.cloud, now=50000, dry_run=False)
    def test_bucket_project_and_cross_project_names_rejected(self):
        for kind,name in [('bucket','projects/research-project/buckets/evidence'), ('project','projects/research-project'), ('instance','projects/other-project/zones/us-central1-a/instances/controller')]:
            self.record['resources'][0].update(kind=kind,name=name)
            with self.assertRaises(ValueError):
                self.mod.enforce(self.record,self.cloud,now=43300,dry_run=False)
    def test_missing_already_deleted_is_idempotent(self):
        self.cloud.resource = None
        result = self.mod.enforce(self.record,self.cloud,now=43300,dry_run=False)
        self.assertEqual(len(result['absent']),1)

class FakeCloud:
    def __init__(self):
        self.resource={'uid':'123','owner_id':'research','run_id':'run-1'}
        self.deleted=[]
    def verify_lease(self, lease): return True
    def list_owned_disks(self, record): return []  # Explicit empty-disk test protocol.
    def get(self, resource): return self.resource
    def delete(self, resource): self.deleted.append(resource)

class RestDeletionTests(unittest.TestCase):
    def test_second_check_refuses_changed_owner(self):
        mod=importlib.util.module_from_spec(SPEC)
        SPEC.loader.exec_module(mod)
        cloud=mod.GCPCloud(None)
        cloud.get=lambda resource: {'uid':'123','owner_id':'stranger','run_id':'run-1'}
        with self.assertRaises(ValueError):
            cloud.delete({'kind':'instance','name':'projects/research-project/zones/us-central1-a/instances/controller','uid':'123','owner_id':'research','run_id':'run-1'})

class ExtendedInventoryTests(unittest.TestCase):
    def test_owned_disks_and_router_are_supported(self):
        mod=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(mod)
        cloud=FakeCloud()
        record={'project_id':'research-project','owner_id':'research','run_id':'run-1','started_at':0,'deadline':43200,'resources':[
          {'kind':'disk','name':'projects/research-project/zones/us-central1-a/disks/raw','uid':'123'},
          {'kind':'router','name':'projects/research-project/regions/us-central1/routers/eval','uid':'123'}]}
        result=mod.enforce(record,cloud,now=43200,dry_run=False)
        self.assertEqual(len(result['cleanup_actions_requested']),1)
        self.assertTrue(result['deferred'])
    def test_empty_inventory_rejected(self):
        mod=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(mod)
        with self.assertRaises(ValueError):
            mod.enforce({'project_id':'research-project','owner_id':'research','run_id':'run-1','started_at':0,'deadline':43200,'resources':[]},FakeCloud(),now=43200)
    def test_one_failure_does_not_skip_other_cleanup(self):
        mod=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(mod)
        cloud=FakeCloud()
        def delete(resource):
            if resource['kind']=='cluster': raise RuntimeError('cloud temporarily unavailable')
            cloud.deleted.append(resource)
        cloud.delete=delete
        record={'project_id':'research-project','owner_id':'research','run_id':'run-1','started_at':0,'deadline':43200,'resources':[
          {'kind':'cluster','name':'projects/research-project/locations/us-central1-a/clusters/app','uid':'123'},
          {'kind':'instance','name':'projects/research-project/zones/us-central1-a/instances/controller','uid':'123'}]}
        result=mod.enforce(record,cloud,now=43200,dry_run=False)
        self.assertEqual(len(cloud.deleted),1)
        self.assertTrue(result['errors'])

class HandlerResponseTests(unittest.TestCase):
    def test_rejected_method_is_json_with_cleanup_limitation(self):
        import json
        from types import SimpleNamespace
        mod=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(mod)
        body,status,headers=mod.handler(SimpleNamespace(method='GET'))
        self.assertEqual(status,405)
        self.assertEqual(headers['Content-Type'],'application/json')
        result=json.loads(body)
        self.assertTrue(result['not_hard_guarantee'])
        self.assertIn('residual',result)
    def test_before_deadline_residual_lists_unchecked_inventory(self):
        mod=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(mod)
        name='projects/research-project/zones/us-central1-a/instances/controller'
        record={'project_id':'research-project','owner_id':'research','run_id':'run-1','started_at':0,'deadline':43200,'resources':[{'kind':'instance','name':name,'uid':'123'}]}
        result=mod.enforce(record,FakeCloud(),now=1,dry_run=False)
        self.assertEqual(result.get('residual'),[name])

class FinalCleanupTests(unittest.TestCase):
    def module(self):
        mod=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(mod);return mod
    def record(self):
        return {'project_id':'research-project','owner_id':'research','run_id':'run-1','started_at':0,'deadline':43200,
          'completed_export_verified':True,'cleanup_requested_at':100,'export_receipt':{'objects':[{'bucket':'evidence','object':'runs/run-1/final-summary/run-report.json','generation':'1','sha256':'a'*64}]},
          'resources':[{'kind':'instance','name':'projects/research-project/zones/us-central1-a/instances/controller','uid':'123'},
                       {'kind':'router','name':'projects/research-project/regions/us-central1/routers/eval','uid':'123'},
                       {'kind':'firewall','name':'projects/research-project/global/firewalls/eval','uid':'123'},
                       {'kind':'subnetwork','name':'projects/research-project/regions/us-central1/subnetworks/eval','uid':'123'},
                       {'kind':'network','name':'projects/research-project/global/networks/eval','uid':'123'}]}
    def test_early_cleanup_requires_independently_verified_export(self):
        cloud=FakeCloud();cloud.verify_export=lambda receipt:True
        result=self.module().enforce(self.record(),cloud,now=200,dry_run=False)
        self.assertEqual([r['kind'] for r in cloud.deleted],['instance'])
        self.assertTrue(result['deferred'])
        self.assertTrue(result['completion_cleanup'])
    def test_boolean_claim_without_verifier_never_deletes_early(self):
        cloud=FakeCloud()
        result=self.module().enforce(self.record(),cloud,now=200,dry_run=False)
        self.assertEqual(cloud.deleted,[])
        self.assertTrue(result['waiting_for_deadline'])
    def test_network_layers_wait_for_children_confirmed_absent(self):
        cloud=FakeCloud();cloud.verify_export=lambda receipt:True
        record=self.record();cloud.get=lambda resource: None if resource['kind'] in {'instance','router','firewall','subnetwork'} else cloud.resource
        result=self.module().enforce(record,cloud,now=200,dry_run=False)
        self.assertEqual([r['kind'] for r in cloud.deleted],['network'])
    def test_unverified_export_is_refused_before_deadline(self):
        cloud=FakeCloud();cloud.verify_export=lambda receipt:False
        result=self.module().enforce(self.record(),cloud,now=200,dry_run=False)
        self.assertEqual(cloud.deleted,[])
        self.assertTrue(result['residual'])

class ExportProofTests(unittest.TestCase):
    def test_cloud_adapter_rechecks_each_pinned_generation(self):
        import os,hashlib
        from unittest.mock import patch
        from types import SimpleNamespace
        payload=b'{"state":"complete"}'
        seal=(hashlib.sha256(payload).hexdigest()+'  run-report.json\n').encode()
        class Session:
            def __init__(self):self.calls=[]
            def get(self,url,**kwargs):
                self.calls.append((url,kwargs));return SimpleNamespace(content=seal if 'GKE-SHA256SUMS' in url else payload,raise_for_status=lambda:None)
        mod=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(mod)
        session=Session();cloud=mod.GCPCloud(session)
        receipt={'uri':'gs://evidence/gke-qualification-v1/runs/run-1/final-summary/','objects':[{'bucket':'evidence','object':'gke-qualification-v1/runs/run-1/final-summary/'+name,'generation':'123','sha256':hashlib.sha256(seal if name=='GKE-SHA256SUMS' else payload).hexdigest()} for name in ['GKE-SHA256SUMS','run-report.json']]}
        with patch.dict(os.environ,{'INVENTORY_BUCKET':'evidence'}):
            self.assertTrue(cloud.verify_export(receipt))
            self.assertEqual(len(session.calls),2)
            self.assertTrue(all(call[1]['params']['generation']=='123' for call in session.calls))
            receipt['objects'][1]['sha256']='0'*64
            self.assertFalse(cloud.verify_export(receipt))
    def test_deadline_cleanup_does_not_depend_on_export_proof_availability(self):
        mod=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(mod)
        cloud=FakeCloud();cloud.verify_export=lambda receipt: (_ for _ in ()).throw(RuntimeError('export unavailable'))
        record=FinalCleanupTests().record()
        result=mod.enforce(record,cloud,now=43200,dry_run=False)
        self.assertEqual([r['kind'] for r in cloud.deleted],['instance'])
        self.assertFalse(result['waiting_for_deadline'])

class WatchdogLeaseTests(unittest.TestCase):
    def test_lease_release_uses_current_generation_and_exact_run_token(self):
        import os
        from unittest.mock import patch
        from types import SimpleNamespace
        class Session:
            def __init__(self):self.deleted=[]
            def get(self,url,params=None,**kwargs):
                value={'generation':'7'} if not params else {'token':'a'*32,'project_id':'research-project','owner_id':'gke-qualification-v1','run_id':'run-1','phase':'cleanup-requested'}
                return SimpleNamespace(status_code=200,json=lambda:value,raise_for_status=lambda:None)
            def delete(self,url,params=None,**kwargs):
                self.deleted.append((url,params));return SimpleNamespace(raise_for_status=lambda:None)
        mod=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(mod)
        session=Session();cloud=mod.GCPCloud(session,expected_run='run-1')
        lease={'bucket':'private-state','object':'control/active-run.json','token':'a'*32,'generation':'3'}
        with patch.dict(os.environ,{'STATE_BUCKET':'private-state','PROJECT_ID':'research-project'}):
            self.assertTrue(cloud.verify_lease(lease))
            self.assertTrue(cloud.release_lease(lease))
            self.assertEqual(session.deleted[0][1],{'ifGenerationMatch':'7'})
            lease['token']='b'*32
            self.assertFalse(cloud.verify_lease(lease))
            with self.assertRaises(ValueError):cloud.release_lease(lease)
            self.assertEqual(len(session.deleted),1)

class CreationIntentTests(unittest.TestCase):
    def test_deadline_resolves_and_binds_first_owned_uid_before_delete(self):
        mod=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(mod)
        intent={'kind':'cluster','name':'projects/research-project/locations/us-central1-a/clusters/eval','intent_id':'a'*32,'binding_object':'control/run-1/uid-bindings/'+'a'*32+'.json'}
        record={'project_id':'research-project','owner_id':'research','run_id':'run-1','started_at':0,'deadline':43200,'resources':[],'creation_intents':[intent]}
        cloud=FakeCloud();events=[]
        def reconcile(value,record,*,dry_run):
            events.append('bind');return {'kind':value['kind'],'name':value['name'],'uid':'123'}
        cloud.reconcile_intent=reconcile
        original=cloud.delete
        cloud.delete=lambda resource:(events.append('delete'),original(resource))
        result=mod.enforce(record,cloud,now=43200,dry_run=False)
        self.assertEqual(events,['bind','delete'])
        self.assertEqual(result['unresolved_creation_intents'],[])
    def test_unobserved_creation_stays_residual_and_prevents_lease_release_proof(self):
        mod=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(mod)
        intent={'kind':'cluster','name':'projects/research-project/locations/us-central1-a/clusters/eval','intent_id':'a'*32,'binding_object':'control/run-1/uid-bindings/'+'a'*32+'.json'}
        record={'project_id':'research-project','owner_id':'research','run_id':'run-1','started_at':0,'deadline':43200,'resources':[],'creation_intents':[intent]}
        cloud=FakeCloud();cloud.reconcile_intent=lambda *args,**kwargs:None
        result=mod.enforce(record,cloud,now=43200,dry_run=False)
        self.assertTrue(result['residual']);self.assertTrue(result['unresolved_creation_intents'])
        self.assertEqual(cloud.deleted,[])

class ImmutableIntentBindingTests(unittest.TestCase):
    def test_cas_binding_precedes_cleanup_and_replaced_uid_never_rebinds(self):
        import os,json
        from unittest.mock import patch
        from types import SimpleNamespace
        class Session:
            def __init__(self):self.value=None;self.posts=[]
            def get(self,url,params=None,**kwargs):
                value=self.value if params else {'generation':'9'}
                return SimpleNamespace(status_code=200 if self.value else 404,json=lambda:value,raise_for_status=lambda:None)
            def post(self,url,**kwargs):
                self.posts.append(kwargs);self.value=json.loads(kwargs['data']);return SimpleNamespace(status_code=200,raise_for_status=lambda:None)
        mod=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(mod)
        session=Session();cloud=mod.GCPCloud(session,expected_run='run-1');cloud.verify_lease=lambda lease:True
        actual={'uid':'first-uid','owner_id':'gke-qualification-v1','run_id':'run-1'};cloud.get=lambda resource:actual
        intent={'kind':'cluster','name':'projects/research-project/locations/us-central1-a/clusters/eval','intent_id':'a'*32,'binding_object':'control/run-1/uid-bindings/'+'a'*32+'.json'}
        record={'project_id':'research-project','owner_id':'gke-qualification-v1','run_id':'run-1','lease':{'token':'b'*32}}
        with patch.dict(os.environ,{'INVENTORY_BUCKET':'evidence'}):
            self.assertIsNone(cloud.reconcile_intent(intent,record,dry_run=True))
            self.assertEqual(session.posts,[])
            binding=cloud.reconcile_intent(intent,record,dry_run=False)
            self.assertEqual(binding['uid'],'first-uid')
            self.assertEqual(session.posts[0]['params']['ifGenerationMatch'],'0')
            actual['uid']='replacement-uid'
            with self.assertRaises(ValueError):cloud.reconcile_intent(intent,record,dry_run=False)
            self.assertEqual(len(session.posts),1,'immutable first UID must never be overwritten')

class AbsentIntentNetworkCleanupTests(unittest.TestCase):
    def test_absent_vm_intent_does_not_strand_owned_router_or_pause_watch(self):
        import json,os,sys,types
        from unittest.mock import patch
        from types import SimpleNamespace
        mod=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(mod)
        intent={'kind':'instance','name':'projects/research-project/zones/us-central1-a/instances/controller','intent_id':'a'*32,'binding_object':'control/run-1/uid-bindings/'+'a'*32+'.json'}
        router={'kind':'router','name':'projects/research-project/regions/us-central1/routers/eval','uid':'123'}
        record={'project_id':'research-project','owner_id':'research','run_id':'run-1','started_at':0,'deadline':43200,'resources':[router],'creation_intents':[intent],'lease':{'token':'b'*32}}
        class Session:
            def __init__(self):self.posts=[]
            def get(self,*args,**kwargs):return SimpleNamespace(json=lambda:record,raise_for_status=lambda:None)
            def post(self,url,**kwargs):self.posts.append(url);return SimpleNamespace(raise_for_status=lambda:None)
        session=Session();cloud=FakeCloud();cloud.verify_lease=lambda lease:True
        cloud.reconcile_intent=lambda *args,**kwargs:None # Successful cloud lookup: VM never observed.
        cloud.get=lambda resource:None if cloud.deleted else cloud.resource
        cloud.release_lease=lambda lease:(_ for _ in ()).throw(AssertionError('unresolved intent must retain lease'))
        google=types.ModuleType('google');auth=types.ModuleType('google.auth');transport=types.ModuleType('google.auth.transport');requests=types.ModuleType('google.auth.transport.requests')
        auth.default=lambda **kwargs:('credential','research-project');requests.AuthorizedSession=lambda credentials:session
        google.auth=auth;auth.transport=transport;transport.requests=requests
        modules={'google':google,'google.auth':auth,'google.auth.transport':transport,'google.auth.transport.requests':requests}
        environment={'INVENTORY_BUCKET':'evidence','INVENTORY_OBJECT':'control/run-1/inventory.json','INVENTORY_GENERATION':'123','PROJECT_ID':'research-project','SCHEDULER_NAME':'projects/research-project/locations/us-central1/jobs/watchdog'}
        with patch.dict(sys.modules,modules),patch.dict(os.environ,environment),patch.object(mod,'GCPCloud',lambda *a,**kw:cloud):
            body,status,_=mod._handle(SimpleNamespace(headers={}))
            result=json.loads(body);self.assertEqual(status,200)
            self.assertEqual([item['kind'] for item in cloud.deleted],['router'])
            self.assertIn(intent['name'],result['unresolved_creation_intents'])
            self.assertTrue(result['residual'])
            body,_,_=mod._handle(SimpleNamespace(headers={}))
            result=json.loads(body)
            self.assertIn(router['name'],result['absent'])
            self.assertIn(intent['name'],result['residual'])
            self.assertFalse(result.get('scheduler_paused',False))
            self.assertFalse(result.get('lease_released',False))
            self.assertFalse(any('cloudscheduler.googleapis.com' in url for url in session.posts))
