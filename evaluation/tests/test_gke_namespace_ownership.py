import copy
import json
import unittest
from types import SimpleNamespace

from evaluation.gke import setup
from evaluation.gke.render import transform


class NamespaceOwnershipTests(unittest.TestCase):
    def namespace(self):
        document = transform([{'apiVersion':'v1','kind':'Namespace','metadata':{'name':'eval-boutique'}}],
                             {}, run_id='run-one', mode='demo')[0]
        document['metadata']['uid']='namespace-uid-1'
        return document

    def context(self, document):
        def read(*args, **kwargs):
            self.assertEqual(args, ('get','namespace','eval-boutique','--ignore-not-found','-o','json'))
            return SimpleNamespace(returncode=0,stdout=json.dumps(copy.deepcopy(document)) if document else '')
        return SimpleNamespace(run_id='run-one',state={'owned_namespace_uids':{'eval-boutique':'namespace-uid-1'}},kube=read)

    def test_gke_rendered_namespace_uses_cloud_owner_and_saved_uid(self):
        result=setup.verify_namespace_ownership(self.context(self.namespace()),['eval-boutique'])
        self.assertTrue(result['safe'])
        self.assertEqual(result['namespaces']['eval-boutique']['uid'],'namespace-uid-1')

    def test_foreign_local_wrong_run_changed_uid_and_terminating_namespace_rejected(self):
        changes=[('foreign-owner',{'labels':{'evaluation.karthos.dev/owner':'foreign','evaluation.karthos.dev/run-id':'run-one'}}),
                 ('local-owner',{'labels':{'evaluation.karthos.dev/owner':'karthos-evaluation','evaluation.karthos.dev/run-id':'run-one'}}),
                 ('wrong-run',{'labels':{'evaluation.karthos.dev/owner':'gke-qualification-v1','evaluation.karthos.dev/run-id':'run-two'}}),
                 ('changed-uid',{'uid':'namespace-uid-2'}),
                 ('deleting',{'deletionTimestamp':'2026-11-12T00:00:00Z'}),('missing-uid',{'uid':None})]
        for label,change in changes:
            with self.subTest(label=label):
                document=self.namespace();document['metadata'].update(change)
                with self.assertRaises(ValueError):
                    setup.verify_namespace_ownership(self.context(document),['eval-boutique'])

    def test_failed_setup_without_snapshot_still_checks_owner_and_reports_observed_only(self):
        ctx=self.context(self.namespace());ctx.state={}
        result=setup.verify_namespace_ownership(ctx,['eval-boutique'])
        self.assertEqual(result['namespaces']['eval-boutique']['uid_basis'],'observed-only')

    def test_missing_namespace_allowed_only_without_saved_uid(self):
        ctx=self.context(None)
        with self.assertRaises(ValueError):
            setup.verify_namespace_ownership(ctx,['eval-boutique'],allow_absent=True)
        result=setup.verify_namespace_ownership(ctx,['eval-boutique'],expected_uids={},allow_absent=True)
        self.assertEqual(result['absent'],['eval-boutique'])

    def test_explicit_active_snapshot_ignores_stale_state(self):
        ctx=self.context(self.namespace());ctx.state['owned_namespace_uids']['eval-boutique']='stale-uid'
        result=setup.verify_namespace_ownership(ctx,['eval-boutique'],expected_uids={'eval-boutique':'namespace-uid-1'})
        self.assertEqual(result['namespaces']['eval-boutique']['uid_basis'],'setup-snapshot')

    def test_successful_setup_snapshot_replaces_previous_cell_uids(self):
        written={}
        def read(*args,**kwargs):
            name=args[2]
            if args[1]=='configmap':
                from evaluation import runner as r
                document={'kind':'ConfigMap','metadata':{'name':r.MARKER,'namespace':r.KARTHOS_NAMESPACE,'uid':'marker-uid','labels':{r.OWNER_LABEL:r.OWNER_VALUE}},'data':{'run':'run-one'}}
            else:
                document=self.namespace();document['metadata'].update(name=name,uid=name+'-uid')
            return SimpleNamespace(returncode=0,stdout=json.dumps(document))
        ctx=SimpleNamespace(run_id='run-one',state={'owned_namespace_uids':{'old-demo':'old-uid'}},kube=read,
                            write=lambda path,value:written.update({str(path):value}))
        cell=SimpleNamespace(namespace='eval-boutique',source='mesh')
        result=setup.record_namespace_ownership(ctx,cell,'/review/cell')
        self.assertEqual(set(result['namespace_uids']),{'karthos-system','eval-boutique','istio-system'})
        self.assertNotIn('old-demo',ctx.state['owned_namespace_uids'])
        self.assertEqual(written['/review/cell/namespace-ownership.json'],result)

    def test_marker_checks_legacy_label_exact_run_and_retained_uid(self):
        from evaluation import runner as r
        namespace=self.namespace();namespace['metadata']['name']=r.KARTHOS_NAMESPACE
        original={'kind':'ConfigMap','metadata':{'name':r.MARKER,'namespace':r.KARTHOS_NAMESPACE,'uid':'marker-uid','labels':{r.OWNER_LABEL:r.OWNER_VALUE}},'data':{'run':'run-one'}}
        for change in ('valid','wrong-run','foreign-owner','changed-uid','missing'):
            with self.subTest(change=change):
                marker=copy.deepcopy(original)
                if change=='wrong-run':marker['data']['run']='other-run'
                if change=='foreign-owner':marker['metadata']['labels'][r.OWNER_LABEL]='foreign'
                if change=='changed-uid':marker['metadata']['uid']='replacement'
                def query(*args,**kwargs):
                    value=namespace if args[1]=='namespace' else marker
                    return SimpleNamespace(returncode=0,stdout='' if args[1]=='configmap' and change=='missing' else json.dumps(value))
                ctx=SimpleNamespace(run_id='run-one',state={},kube=query)
                if change=='valid':
                    result=setup.verify_namespace_ownership(ctx,[r.KARTHOS_NAMESPACE],expected_uids={},marker_required=True,expected_marker_uid='marker-uid')
                    self.assertEqual(result['marker']['state'],'verified')
                else:
                    with self.assertRaises(ValueError):
                        setup.verify_namespace_ownership(ctx,[r.KARTHOS_NAMESPACE],expected_uids={},marker_required=True,expected_marker_uid='marker-uid')

    def test_transition_uses_cloud_owner_and_retained_setup_identity(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from unittest.mock import patch
        from evaluation.gke import recoverable_run as recovery
        from evaluation.gke.models import write
        from evaluation import runner as r
        namespace=self.namespace();namespace['metadata'].update(name=r.KARTHOS_NAMESPACE,uid='saved-namespace')
        marker={'kind':'ConfigMap','metadata':{'name':r.MARKER,'namespace':r.KARTHOS_NAMESPACE,'uid':'saved-marker','labels':{r.OWNER_LABEL:r.OWNER_VALUE}},'data':{'run':'run-one'}}
        def query(*args,**kwargs):
            value=namespace if args[1]=='namespace' else marker
            return SimpleNamespace(returncode=0,stdout=json.dumps(value))
        with TemporaryDirectory() as temporary:
            root=Path(temporary);setup_path=root/'setup';setup_path.mkdir();window=root/'window';window.mkdir()
            write(setup_path/'namespace-ownership.json',{'namespace_uids':{r.KARTHOS_NAMESPACE:'saved-namespace'},'marker':{'uid':'saved-marker'}})
            ctx=SimpleNamespace(run_id='run-one',state={},kube=query,result=root,write=write)
            driver=SimpleNamespace(paths={'w-a1':window},setup_path=setup_path)
            with patch('evaluation.gke.provision.require_context'),patch('evaluation.gke.provision.assert_project_lease'),patch('evaluation.runner.check_ownership') as legacy,patch.object(recovery,'_generator_barrier',return_value={'safe':True}):
                result=recovery.transition(ctx,driver,{'id':'w-a1','logical_id':'w','setup_completed':True},{})
            self.assertTrue(result['safe'],result)
            legacy.assert_called_once_with(False,check_namespaces=False)
            self.assertEqual(result['conditions']['ownership']['marker']['uid'],'saved-marker')
