"""Read-only policy gate; fixtures never invoke a provider."""
import copy,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from evaluation.gke import quarantine as q, provision as p
from evaluation.gke.models import canonical_sha
from evaluation.tests import test_gke_foundation_reuse as foundation_tests

class QuarantinePolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.ctx=SimpleNamespace(run_id='run-1',work=Path(self.temp.name),
            config={'execution_profile':'thesis-robustness-full-v1','quarantine':{'bucket':'private-bucket','prefix':q.PREFIX+'run-1/'}},
            state={'terraform':{'foundation':{'quarantine_bucket':'private-bucket','evidence_bucket':'normal-bucket','controller_service_account':'controller@example.iam.gserviceaccount.com'}}})
        self.expected=q.iam_proposal('private-bucket','run-1','serviceAccount:controller@example.iam.gserviceaccount.com')
        self.policy={'version':3,'bindings':[{'role':b['role'],'members':[b['member']],'condition':b['condition']} for b in self.expected]}
        self.calls=[]
        def command(args,**kwargs):
            self.calls.append((args,kwargs))
            return SimpleNamespace(returncode=0,stdout=json.dumps(self.policy),stderr='')
        self.ctx.command=command
    def test_exact_grants_receipt_is_not_effective_permission_proof(self):
        receipt=q.verify_preprovision_policy(self.ctx)
        self.assertTrue(receipt['policy_bindings_verified'])
        self.assertFalse(receipt['remote_permissions_verified'])
        self.assertEqual(receipt['iam_bindings'],self.expected)
        self.assertEqual(receipt['policy_sha256'],canonical_sha(self.policy))
        self.assertEqual(self.calls[0][0],['gcloud','storage','buckets','get-iam-policy','gs://private-bucket','--format=json'])
        self.assertEqual(json.loads((self.ctx.work/'quarantine-policy-preflight.json').read_text()),receipt)
    def test_missing_mismatched_and_broader_grants_fail_closed(self):
        original=copy.deepcopy(self.policy)
        variants=[{'bindings':[]},{'bindings':original['bindings'][:1]},None,{'bindings':None}]
        for change in ('member','run','unconditional','broad'):
            policy=copy.deepcopy(original)
            for binding in policy['bindings']:
                if change=='member':binding['members']=['serviceAccount:other@example.iam.gserviceaccount.com']
                elif change=='run':binding['condition']['expression']=binding['condition']['expression'].replace('run-1','run-2')
                elif change=='unconditional':binding.pop('condition')
                else:binding['condition']['expression']="resource.name.startsWith('projects/_/buckets/private-bucket/objects/')"
            variants.append(policy)
        for policy in variants:
            with self.subTest(policy=policy):
                self.policy=policy
                with self.assertRaises(ValueError):q.verify_preprovision_policy(self.ctx)
                self.assertNotIn('quarantine_policy_preflight',self.ctx.state)
    def test_read_failure_propagates(self):
        def denied(*args,**kwargs):raise RuntimeError('provider read denied')
        self.ctx.command=denied
        with self.assertRaisesRegex(RuntimeError,'provider read denied'):q.verify_preprovision_policy(self.ctx)
        self.assertFalse((self.ctx.work/'quarantine-policy-preflight.json').exists())
    def test_nonzero_read_fails_even_if_stdout_contains_valid_policy(self):
        self.ctx.command=lambda *a,**k:SimpleNamespace(returncode=1,stdout=json.dumps(self.policy),stderr='denied')
        with self.assertRaises(RuntimeError):q.verify_preprovision_policy(self.ctx)
    def test_old_profiles_do_not_read_policy(self):
        for profile in ('boutique-complement-v1',None):
            self.ctx.config['execution_profile']=profile
            self.assertIsNone(q.verify_preprovision_policy(self.ctx))
        self.assertFalse(self.calls)
    def test_missing_quarantine_rejected(self):
        del self.ctx.config['quarantine']
        with self.assertRaises(ValueError):q.verify_preprovision_policy(self.ctx)

class QuarantinePolicyOrderingTests(unittest.TestCase):
    setUp=foundation_tests.FoundationReuseTests.setUp
    def test_missing_grants_stop_after_foundation_before_lease_or_compute(self):
        self.ctx.config['execution_profile']='thesis-robustness-full-v1'
        self.ctx.config['quarantine']={'bucket':'private-bucket','prefix':q.PREFIX+self.ctx.run_id+'/'}
        self.ctx.outputs['foundation']['quarantine_bucket']='private-bucket'
        self.reference['expected_outputs']['quarantine_bucket']='private-bucket'
        original=self.ctx.command
        def command(args,**kwargs):
            if 'get-iam-policy' in args:
                self.assertEqual(self.ctx.state['phase'],'foundation-reused')
                self.ctx.calls.append(args)
                return SimpleNamespace(returncode=0,stdout='{"bindings":[]}',stderr='')
            return original(args,**kwargs)
        self.ctx.command=command
        with patch.object(p,'acquire_project_lease') as lease:
            with self.assertRaisesRegex(ValueError,'quarantine.*bindings'):p.provision(self.ctx)
            lease.assert_not_called()
        self.assertNotIn('creation_intents',self.ctx.state)
        self.assertFalse(any('apply' in c for c in self.ctx.calls))
        self.assertTrue(any('get-iam-policy' in c for c in self.ctx.calls))

    def test_verified_policy_precedes_lease_and_controller_creation(self):
        self.ctx.config['execution_profile']='thesis-robustness-full-v1'
        self.ctx.config['quarantine']={'bucket':'private-bucket','prefix':q.PREFIX+self.ctx.run_id+'/'}
        self.ctx.outputs['foundation']['quarantine_bucket']='private-bucket'
        self.reference['expected_outputs']['quarantine_bucket']='private-bucket'
        original=self.ctx.command
        member='serviceAccount:'+self.ctx.outputs['foundation']['controller_service_account']
        policy={'bindings':[{'role':b['role'],'members':[b['member']],'condition':b['condition']}
            for b in q.iam_proposal('private-bucket',self.ctx.run_id,member)]}
        def command(args,**kwargs):
            if 'get-iam-policy' in args:
                self.assertEqual(self.ctx.state['phase'],'foundation-reused')
                self.assertNotIn('lease',self.ctx.state)
                self.ctx.calls.append(args)
                return SimpleNamespace(returncode=0,stdout=json.dumps(policy),stderr='')
            if 'apply' in args:
                self.assertTrue(self.ctx.state['quarantine_policy_preflight']['policy_bindings_verified'])
            return original(args,**kwargs)
        self.ctx.command=command
        p.provision(self.ctx)
        self.assertEqual(self.ctx.state['phase'],'provisioned')
        self.assertFalse(self.ctx.state['quarantine_policy_preflight']['remote_permissions_verified'])
