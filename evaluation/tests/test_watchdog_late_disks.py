"""Deadline disk discovery is explicit, UID-bound and independent of stale freezes."""
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]


def module():
    spec=importlib.util.spec_from_file_location('watchdog_late_disks',ROOT/'infrastructure/watchdog/main.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod


class LateDiskCloud:
    def __init__(self):
        self.disks=[];self.actions=[];self.lease=True;self.bindings={};self.late=False
    def verify_lease(self,lease):return self.lease
    def list_owned_disks(self,record):
        self.actions.append('list')
        return list(self.disks)
    def reconcile_intent(self,intent,record,*,dry_run):
        self.actions.append('observe')
        if dry_run:return None
        uid=intent['observed_uid'];old=self.bindings.setdefault(intent['intent_id'],uid)
        if old!=uid:raise ValueError('immutable UID changed')
        self.actions.append('bind')
        return {'kind':'disk','name':intent['name'],'uid':old}
    def get(self,resource):
        if resource['kind']=='disk' and not any(row['name']==resource['name'] for row in self.disks):return None
        return {'uid':resource['uid'],'owner_id':'gke-qualification-v1','run_id':'run-1'}
    def delete(self,resource):
        self.actions.append('delete-'+resource['kind'])
        if resource['kind']=='cluster' and self.late:self.disks=[disk()]


def disk(uid='first'):
    return {'kind':'disk','name':'projects/research-project/zones/us-central1-a/disks/pvc-late','uid':uid}


def record():
    return {'project_id':'research-project','owner_id':'gke-qualification-v1','run_id':'run-1',
            'started_at':0,'deadline':43200,'lease':{'token':'a'*32},
            'resources':[{'kind':'cluster','name':'projects/research-project/locations/us-central1-a/clusters/eval','uid':'cluster'}]}


class WatchdogLateDisksTests(unittest.TestCase):
    def test_no_list_or_binding_before_deadline(self):
        cloud=LateDiskCloud();cloud.disks=[disk()]
        result=module().enforce(record(),cloud,now=43199,dry_run=False)
        self.assertTrue(result['waiting_for_deadline']);self.assertEqual(cloud.actions,[])

    def test_predeadline_dry_run_checks_list_permission_without_binding(self):
        cloud=LateDiskCloud();cloud.disks=[disk()]
        result=module().enforce(record(),cloud,now=1,dry_run=True)
        self.assertTrue(result['disk_discovery_verified'])
        self.assertEqual(cloud.actions,['list']);self.assertEqual(cloud.bindings,{})
        cloud.list_owned_disks=lambda _:(_ for _ in ()).throw(PermissionError('compute.disks.list denied'))
        result=module().enforce(record(),cloud,now=1,dry_run=True)
        self.assertFalse(result['disk_discovery_verified']);self.assertTrue(result['errors'])
        self.assertIn('owned-disk-discovery-unverified',result['residual'])
        self.assertEqual(cloud.bindings,{})

    def test_disk_created_after_freeze_is_bound_before_deadline_delete(self):
        cloud=LateDiskCloud();cloud.disks=[disk()]
        result=module().enforce(record(),cloud,now=43200,dry_run=False)
        self.assertLess(cloud.actions.index('bind'),cloud.actions.index('delete-disk'))
        self.assertEqual(len(cloud.bindings),1)
        self.assertEqual(result['inventory_resource_count'],2)

    def test_disk_appearing_during_cluster_delete_is_discovered(self):
        cloud=LateDiskCloud();cloud.late=True
        result=module().enforce(record(),cloud,now=43200,dry_run=False)
        self.assertEqual(cloud.actions.count('list'),2)
        self.assertIn('delete-disk',cloud.actions)
        self.assertIn(disk()['name'],result['residual'])

    def test_dry_run_lists_and_observes_without_binding_or_deletion(self):
        cloud=LateDiskCloud();cloud.disks=[disk()]
        result=module().enforce(record(),cloud,now=43200,dry_run=True)
        self.assertEqual(cloud.bindings,{})
        self.assertFalse(any(a.startswith('delete-') for a in cloud.actions))
        self.assertIn(disk()['name'],result['residual'])

    def test_list_failure_or_unknown_protocol_cannot_prove_absence(self):
        for cloud in [SimpleNamespace(verify_lease=lambda _:True),LateDiskCloud()]:
            if isinstance(cloud,LateDiskCloud):cloud.list_owned_disks=lambda _:(_ for _ in ()).throw(RuntimeError('denied'))
            result=module().enforce(record(),cloud,now=43200,dry_run=False)
            self.assertIn('owned-disk-discovery-unverified',result['residual'])
            self.assertTrue(result['errors'])

    def test_lease_refusal_prevents_listing_or_delete(self):
        cloud=LateDiskCloud();cloud.lease=False
        result=module().enforce(record(),cloud,now=43200,dry_run=False)
        self.assertEqual(cloud.actions,[]);self.assertTrue(result['residual'])

    def test_known_uid_replacement_and_binding_failure_prevent_delete(self):
        cloud=LateDiskCloud();cloud.disks=[disk('replacement')];r=record();r['resources'].append(disk())
        result=module().enforce(r,cloud,now=43200,dry_run=False)
        self.assertTrue(result['errors']);self.assertFalse(any(a.startswith('delete-') for a in cloud.actions))
        cloud=LateDiskCloud();cloud.disks=[disk()]
        cloud.reconcile_intent=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('GCS unavailable'))
        result=module().enforce(record(),cloud,now=43200,dry_run=False)
        self.assertTrue(result['errors']);self.assertNotIn('delete-disk',cloud.actions)

    def test_post_compute_list_failure_retains_residual(self):
        cloud=LateDiskCloud();calls=[0]
        def listing(_):
            calls[0]+=1
            if calls[0]>1:raise RuntimeError('second list failed')
            return []
        cloud.list_owned_disks=listing
        result=module().enforce(record(),cloud,now=43200,dry_run=False)
        self.assertIn('owned-disk-discovery-unverified',result['residual'])

    def test_rest_filters_paginates_and_refuses_repeated_token(self):
        mod=module();calls=[]
        row={'id':'123','labels':{'evaluation-owner':'gke-qualification-v1','evaluation-run':'run-1'},
             'selfLink':'https://www.googleapis.com/compute/v1/'+disk()['name']}
        pages=[{'items':[row],'nextPageToken':'next'},{'items':[]}]
        def get(url,**kwargs):
            calls.append((url,kwargs));page=pages.pop(0)
            return SimpleNamespace(raise_for_status=lambda:None,json=lambda:page)
        cloud=mod.GCPCloud(SimpleNamespace(get=get));cloud.verify_lease=lambda _:True
        self.assertEqual(cloud.list_owned_disks(record()),[dict(disk(),uid='123')])
        self.assertIn('(labels.evaluation-owner = "gke-qualification-v1") AND (labels.evaluation-run = "run-1")',calls[0][1]['params']['filter'])
        self.assertEqual(calls[1][1]['params']['pageToken'],'next')
        self.assertTrue(all(k['params']['maxResults']==500 for _,k in calls))
        pages.extend([{'nextPageToken':'same'},{'nextPageToken':'same'}])
        with self.assertRaisesRegex(ValueError,'repeated'):cloud.list_owned_disks(record())

    def test_rest_rejects_wrong_labels_project_location_and_page_size(self):
        mod=module();good={'id':'123','labels':{'evaluation-owner':'gke-qualification-v1','evaluation-run':'run-1'},
                          'selfLink':'https://www.googleapis.com/compute/v1/'+disk()['name']}
        for bad in [{**good,'labels':{}},{**good,'selfLink':good['selfLink'].replace('research-project','other-project')},
                    {**good,'selfLink':good['selfLink'].replace('us-central1-a','us-east1-a')}]:
            cloud=mod.GCPCloud(SimpleNamespace(get=lambda *a,**k:SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'items':[bad]})))
            cloud.verify_lease=lambda _:True
            with self.assertRaises(ValueError):cloud.list_owned_disks(record())
        cloud.session.get=lambda *a,**k:SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'items':[good]*501})
        with self.assertRaises(ValueError):cloud.list_owned_disks(record())

    def test_attached_disk_or_changed_lease_refused_on_immediate_delete_check(self):
        mod=module();cloud=mod.GCPCloud(SimpleNamespace(delete=lambda *a,**k:self.fail('must not delete')))
        cloud.verify_lease=lambda _:True
        cloud.get=lambda _:{'uid':'first','owner_id':'gke-qualification-v1','run_id':'run-1','users':['vm']}
        resource=dict(disk(),owner_id='gke-qualification-v1',run_id='run-1')
        with self.assertRaisesRegex(ValueError,'attached'):cloud.delete(resource)
        cloud.get=lambda _:{'uid':'first','owner_id':'gke-qualification-v1','run_id':'run-1','users':[]}
        cloud.verify_lease=lambda _:False
        with self.assertRaisesRegex(ValueError,'lease mismatch'):cloud.delete(resource)

    def test_discovery_failure_handler_keeps_scheduler_and_lease(self):
        import sys,types
        mod=module();cloud=LateDiskCloud();cloud.list_owned_disks=lambda _:(_ for _ in ()).throw(RuntimeError('list denied'))
        cloud.release_lease=lambda _:self.fail('must retain lease')
        r=record();r['deadline']=1
        posts=[]
        session=SimpleNamespace(get=lambda *a,**k:SimpleNamespace(raise_for_status=lambda:None,json=lambda:r),
                                post=lambda url,**k:(posts.append(url) or SimpleNamespace(raise_for_status=lambda:None)))
        google=types.ModuleType('google');auth=types.ModuleType('google.auth');transport=types.ModuleType('google.auth.transport');requests=types.ModuleType('google.auth.transport.requests')
        auth.default=lambda **k:('credential','research-project');requests.AuthorizedSession=lambda _:session
        google.auth=auth;auth.transport=transport;transport.requests=requests
        modules={'google':google,'google.auth':auth,'google.auth.transport':transport,'google.auth.transport.requests':requests}
        env={'INVENTORY_BUCKET':'evidence','INVENTORY_OBJECT':'inventory.json','INVENTORY_GENERATION':'1','PROJECT_ID':'research-project'}
        with patch.dict(sys.modules,modules),patch.dict(os.environ,env),patch.object(mod,'GCPCloud',return_value=cloud):
            body,_,_=mod._handle(SimpleNamespace(headers={}))
        result=json.loads(body)
        self.assertFalse(result['compute_network_absence_verified'])
        self.assertFalse(any(':pause' in url or ':resume' in url for url in posts))

    def test_pagination_stops_at_one_hundred_pages(self):
        mod=module();calls=[]
        def get(*a,**k):
            calls.append(k)
            return SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'nextPageToken':str(len(calls))})
        cloud=mod.GCPCloud(SimpleNamespace(get=get));cloud.verify_lease=lambda _:True
        with self.assertRaisesRegex(ValueError,'page bound'):cloud.list_owned_disks(record())
        self.assertEqual(len(calls),100)

    def test_discovered_uid_change_refuses_cas_write(self):
        mod=module();writes=[]
        response=SimpleNamespace(status_code=404,raise_for_status=lambda:None)
        session=SimpleNamespace(get=lambda *a,**k:response,post=lambda *a,**k:writes.append(k))
        cloud=mod.GCPCloud(session);cloud.verify_lease=lambda _:True
        cloud.get=lambda _:{'uid':'replacement','owner_id':'gke-qualification-v1','run_id':'run-1'}
        intent={'kind':'disk','name':disk()['name'],'observed_uid':'first','intent_id':'a'*32,
                'binding_object':'control/run-1/uid-bindings/'+'a'*32+'.json'}
        with patch.dict(os.environ,{'INVENTORY_BUCKET':'evidence'}),self.assertRaisesRegex(ValueError,'changed since discovery'):
            cloud.reconcile_intent(intent,record(),dry_run=False)
        self.assertEqual(writes,[])

    def test_foundation_role_grants_disk_listing(self):
        text=(ROOT/'infrastructure/terraform/foundation/main.tf').read_text()
        role=text.split('resource "google_project_iam_custom_role" "watchdog"',1)[1].split('\n}',1)[0]
        self.assertIn('"compute.disks.list"',role)
