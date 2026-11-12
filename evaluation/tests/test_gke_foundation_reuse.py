import hashlib,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from evaluation.gke import provision as p
from evaluation.tests.test_gke_provision import CloudContext

class FoundationReuseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.ctx=CloudContext(Path(self.temp.name))
        self.ctx.outputs['foundation']['backup_bucket']='research-project-backup'
        self.reference={'project_id':'research-project','state_bucket':'research-project-state','prefix':'foundation','lineage':'11111111-2222-3333-4444-555555555555','expected_outputs':dict(self.ctx.outputs['foundation']),'source_files':{str(f.name):hashlib.sha256(f.read_bytes()).hexdigest() for f in (self.ctx.root/'infrastructure/terraform/foundation').iterdir() if f.is_file()}}
        self.ctx.config['gcp']['state_bucket_name']='research-project-state'
        self.ctx.config['gcp']['foundation_reference']=self.reference
        self.plan_exit=0;self.lineage=self.reference['lineage'];self.membership=True
        original=self.ctx.command
        def command(args,**kw):
            args=list(map(str,args))
            if args[:4]==['gcloud','storage','buckets','list']:
                self.ctx.calls.append(args);return SimpleNamespace(returncode=0,stdout=json.dumps([{'name':'research-project-state'}] if self.membership else []),stderr='')
            if args[:4]==['gcloud','storage','buckets','describe']:
                self.ctx.calls.append(args);return SimpleNamespace(returncode=0,stdout=json.dumps({'name':'research-project-state','public_access_prevention':'enforced','uniform_bucket_level_access':True,'versioning_enabled':True}),stderr='')
            if args[0]=='terraform' and 'pull' in args:
                self.ctx.calls.append(args);return SimpleNamespace(returncode=0,stdout=json.dumps({'lineage':self.lineage}),stderr='')
            if args[0]=='terraform' and '-detailed-exitcode' in args:
                self.ctx.calls.append(args);return SimpleNamespace(returncode=self.plan_exit,stdout='',stderr='')
            return original(args,**kw)
        self.ctx.command=command
    def test_reuse_is_no_change_and_keeps_explicit_backend_prefix(self):
        p.reuse_foundation(self.ctx)
        self.assertEqual(self.ctx.state['terraform']['foundation'],self.reference['expected_outputs'])
        self.assertTrue(any('-backend-config=prefix=foundation' in c for c in self.ctx.calls))
        self.assertFalse(any('apply' in c or 'import' in c for c in self.ctx.calls))
    def test_wrong_lineage_rejected_without_apply(self):
        self.lineage='another-state'
        with self.assertRaisesRegex(ValueError,'lineage'):p.reuse_foundation(self.ctx)
    def test_nonzero_plan_rejected_without_changing_foundation(self):
        self.plan_exit=2
        with self.assertRaisesRegex(ValueError,'changes'):p.reuse_foundation(self.ctx)
        self.assertFalse(any('apply' in c for c in self.ctx.calls))
    def test_foreign_project_rejected_before_any_command(self):
        self.reference['project_id']='other-project'
        with self.assertRaises(ValueError):p.reuse_foundation(self.ctx)
        self.assertEqual(self.ctx.calls,[])
    def test_foreign_bucket_membership_rejected(self):
        self.membership=False
        with self.assertRaisesRegex(ValueError,'project'):p.reuse_foundation(self.ctx)
    def test_changed_source_rejected_before_any_command(self):
        (self.ctx.root/'infrastructure/terraform/foundation/main.tf').write_text('changed')
        with self.assertRaisesRegex(ValueError,'source'):p.reuse_foundation(self.ctx)
        self.assertEqual(self.ctx.calls,[])
    def test_provision_reuses_foundation_without_bootstrap_or_foundation_apply(self):
        p.provision(self.ctx)
        self.assertEqual(self.ctx.state['phase'],'provisioned')
        self.assertFalse(any('-migrate-state' in c for c in self.ctx.calls))
        self.assertFalse(any('apply' in c and c[1].endswith('/foundation') for c in self.ctx.calls))
    def test_existing_bucket_without_reference_still_refused(self):
        del self.ctx.config['gcp']['foundation_reference']
        with self.assertRaisesRegex(ValueError,'explicit'):p.provision(self.ctx)
        self.assertFalse(any('apply' in c for c in self.ctx.calls))

    def test_retry_only_before_project_lease_preserves_original_deadline(self):
        p.reuse_foundation(self.ctx)
        started=self.ctx.state['started_at'];deadline=self.ctx.state['deadline']
        result=p.provision(self.ctx)
        self.assertEqual(result['phase'],'provisioned')
        self.assertEqual(self.ctx.state['started_at'],started)
        self.assertEqual(self.ctx.state['deadline'],deadline)
        self.assertFalse(any('apply' in c and c[1].endswith('/foundation') for c in self.ctx.calls))
    def test_retry_rejects_any_possible_runtime_ownership_or_creation(self):
        p.reuse_foundation(self.ctx)
        from copy import deepcopy
        initial=deepcopy(self.ctx.state)
        for key,value in [('lease',{'token':'existing'}),('resources',[{'kind':'instance'}]),('creation_intents',[{'kind':'instance'}]),('watchdog_requested',True),('runtime_requested',True),('phase','provisioned')]:
            self.ctx.state=deepcopy(initial);self.ctx.state[key]=value;self.ctx.calls.clear()
            with self.assertRaisesRegex(ValueError,'existing provisioning state'):p.provision(self.ctx)
            self.assertEqual(self.ctx.calls,[])
