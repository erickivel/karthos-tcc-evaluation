"""Late PVCs must enter an exact UID inventory before operator cleanup."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from evaluation.gke import teardown as t


class LateDiskTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.g={'project_id':'research-project','zone':'us-central1-a','region':'us-central1'}
        self.disk={'id':'disk-uid','name':'pvc-late','labels':{'evaluation-owner':'gke-qualification-v1','evaluation-run':'run-1'},
                   'selfLink':'https://www.googleapis.com/compute/v1/projects/research-project/zones/us-central1-a/disks/pvc-late'}
        self.name=self.disk['selfLink'].split('/compute/v1/')[1]
        self.events=[]
        self.ctx=SimpleNamespace(run_id='run-1',config={'gcp':self.g},
            state={'owner_id':'gke-qualification-v1','run_id':'run-1','started_at':time.time(),'deadline':time.time()+3600,'resources':[],'terraform':{}},
            event=lambda phase,**kw:self.events.append((phase,json.loads(json.dumps(self.ctx.state)))))
        self.lease=patch.object(t,'assert_project_lease');self.lease.start();self.addCleanup(self.lease.stop)
        binder=patch.object(t,'resolve_creation_uid',side_effect=lambda ctx,intent,**kw:{'kind':'disk','name':intent['name'],'uid':intent['observed_uid']})
        binder.start();self.addCleanup(binder.stop)

    def test_first_observation_persists_uid_and_rejects_replacement(self):
        self.ctx.command=lambda *a,**k:SimpleNamespace(stdout=json.dumps([self.disk]))
        with patch.object(t,'verify_resource',return_value={'value':{}}):
            resources=t.refresh_cleanup_disks(self.ctx)
        self.assertEqual(resources,[{'kind':'disk','name':self.name,'uid':'disk-uid'}])
        self.assertEqual(self.events[-1][1]['resources'],resources)
        self.disk['id']='replacement'
        with self.assertRaisesRegex(ValueError,'UID changed'):
            t.refresh_cleanup_disks(self.ctx)

    def test_scope_and_ownership_failure_never_persist(self):
        for key,value in [('labels',{}),('selfLink',self.disk['selfLink'].replace('research-project','another-project')),
                          ('selfLink',self.disk['selfLink'].replace('us-central1-a','us-east1-a'))]:
            with self.subTest(field=key):
                row={**self.disk,key:value}
                self.ctx.command=lambda *a,**k:SimpleNamespace(stdout=json.dumps([row]))
                with self.assertRaises(ValueError):t.refresh_cleanup_disks(self.ctx)
                self.assertEqual(self.events,[])
                self.assertEqual(self.ctx.state['resources'],[])

    def test_late_disk_after_cluster_removal_is_uid_checked_before_delete(self):
        cluster={'kind':'cluster','name':'projects/research-project/locations/us-central1-a/clusters/eval','uid':'cluster-uid'}
        self.ctx.state['resources']=[cluster]
        state={'cluster':True,'disk':True,'lists':0};operations=[]
        def command(args,**kw):
            operations.append(args)
            if args[2:4]==['disks','list']:
                state['lists']+=1
                return SimpleNamespace(stdout=json.dumps([] if state['lists']==1 else [self.disk]))
            if args[2:4]==['disks','delete']:
                self.assertEqual(self.events[-1][0],'cleanup-disks-observed')
                self.assertTrue(any(r.get('uid')=='disk-uid' for r in self.events[-1][1]['resources']))
                state['disk']=False
            return SimpleNamespace(stdout='{}')
        def verify(ctx,resource,**kw):
            return {'value':{}} if state[resource['kind']] else None
        self.ctx.command=command
        with patch.object(t,'verify_resource',side_effect=verify),patch.object(t,'mark_lease_cleanup'),patch.object(t,'cluster_variables',return_value={}),patch.object(t,'delete_bound_cluster',side_effect=lambda *_:state.update(cluster=False)):
            result=t.teardown(self.ctx)
        self.assertEqual(result['residual'],[])
        self.assertEqual(state['lists'],2)
        self.assertTrue(any(args[2:4]==['disks','delete'] for args in operations))

    def test_post_cluster_list_failure_preserves_controls_and_lease(self):
        cluster={'kind':'cluster','name':'projects/research-project/locations/us-central1-a/clusters/eval','uid':'cluster-uid'}
        self.ctx.state['resources']=[cluster]
        calls=[0]
        def command(args,**kw):
            calls[0]+=1
            if calls[0]>1:raise RuntimeError('post-cluster list unavailable')
            return SimpleNamespace(stdout='[]')
        self.ctx.command=command
        with patch.object(t,'verify_resource',return_value={'value':{}}),patch.object(t,'mark_lease_cleanup'),patch.object(t,'cluster_variables',return_value={}),patch.object(t,'delete_bound_cluster'),patch.object(t,'release_project_lease') as release,patch.object(t,'disarm_watchdog') as disarm:
            with self.assertRaisesRegex(RuntimeError,'post-cluster list unavailable'):t.teardown(self.ctx)
        release.assert_not_called();disarm.assert_not_called()

    def test_attached_disk_is_retained_without_delete(self):
        resource={'kind':'disk','name':self.name,'uid':'disk-uid'}
        self.ctx.state['resources']=[resource]
        calls=[]
        self.ctx.command=lambda args,**kw:(calls.append(args) or SimpleNamespace(stdout=json.dumps([self.disk])))
        with patch.object(t,'verify_resource',return_value={'value':{'users':['attached-instance']}}),patch.object(t,'mark_lease_cleanup'):
            result=t.teardown(self.ctx)
        self.assertIn(self.name,result['residual'])
        self.assertTrue(any(error['stage']=='disk' for error in result['errors']))
        self.assertFalse(any(args[2:4]==['disks','delete'] for args in calls))

    def test_cleanup_context_allows_only_exact_run_scoped_disk_list(self):
        ctx=t.CleanupContext.__new__(t.CleanupContext)
        ctx.config={'gcp':self.g};ctx.run_id='run-1';ctx.state={}
        calls=[]
        ctx.executor=lambda args,**kw:(calls.append(args) or SimpleNamespace(returncode=0,stdout='[]',stderr=''))
        args=['gcloud','compute','disks','list','--project=research-project',
              '--filter=labels.evaluation-owner=gke-qualification-v1 AND labels.evaluation-run=run-1','--format=json']
        ctx.command(args,cleanup=True)
        for invalid in [args[:4]+args[5:], [arg.replace('run-1','another-run') for arg in args],
                        [arg.replace('research-project','another-project') for arg in args],args+['--limit=1']]:
            with self.subTest(args=invalid),self.assertRaises(ValueError):ctx.command(invalid,cleanup=True)
        self.assertEqual(len(calls),1)

    def test_list_failure_refuses_cleanup_and_never_releases_lease(self):
        self.ctx.state['resources']=[{'kind':'disk','name':self.name,'uid':'disk-uid'}]
        self.ctx.command=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('list unavailable'))
        with patch.object(t,'release_project_lease') as release,patch.object(t,'disarm_watchdog') as disarm:
            with self.assertRaisesRegex(RuntimeError,'list unavailable'):t.teardown(self.ctx)
        release.assert_not_called();disarm.assert_not_called()
        self.assertEqual(self.events,[])
