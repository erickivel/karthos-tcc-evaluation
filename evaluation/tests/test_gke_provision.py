import importlib.util,json,sys,tempfile,unittest,time
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[2]
# Core modules are supplied by the evaluation package; no external executor runs.
sys.path.insert(0,str(ROOT))

class ProvisionTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((ROOT/'evaluation/gke/provision.py').exists(),'approved provisioning implementation missing')
        from evaluation.gke import provision,teardown
        self.p,self.t=provision,teardown
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.ctx=FakeContext(Path(self.tmp.name))
    def test_foundation_variables_preserve_scoped_operator_grant(self):
        self.ctx.config['gcp']['operator_member']='user:operator@example.invalid'
        self.assertEqual(self.p.foundation_variables(self.ctx)['operator_member'],'user:operator@example.invalid')
        self.assertEqual(self.p.foundation_variables(self.ctx)['state_bucket_name'],'research-project-karthos-eval-state')

    def test_wrong_target_ownership_blocks_before_any_command(self):
        self.ctx.state['owner_id']='someone-else'
        with self.assertRaises(ValueError):self.p.provision(self.ctx)
        self.assertEqual(self.ctx.calls,[])
    def test_actual_billing_mismatch_blocks_mutation(self):
        self.ctx.billing='different-account'
        with self.assertRaises(ValueError):self.p.provision(self.ctx)
        self.assertFalse(any('apply' in c for c in self.ctx.calls))
    def test_insufficient_actual_cpu_quota_blocks_mutation(self):
        self.ctx.cpus=3
        with self.assertRaises(ValueError):self.p.provision(self.ctx)
        self.assertFalse(any('apply' in c for c in self.ctx.calls))
    def test_global_cpu_quota_blocks_even_with_sufficient_regional_quota(self):
        self.ctx.global_cpus=12
        with self.assertRaisesRegex(ValueError,'global CPU quota'):
            self.p.live_preflight(self.ctx)
        self.assertFalse(any('apply' in c for c in self.ctx.calls))
    def test_global_cpu_quota_subtracts_existing_usage(self):
        self.ctx.global_cpus=24;self.ctx.global_cpu_usage=5
        with self.assertRaisesRegex(ValueError,'global CPU quota'):
            self.p.live_preflight(self.ctx)
    def test_global_cpu_quota_accepts_twenty_available_or_absent_limit(self):
        for limit in (20,None):
            self.ctx.global_cpus=limit
            self.p.live_preflight(self.ctx)
            self.assertEqual(self.ctx.state['phase'],'live-preflight-verified')
    def test_balanced_disk_quota_below_boot_disk_floor_blocks(self):
        self.ctx.ssd_gb=250
        with self.assertRaisesRegex(ValueError,'SSD disk quota'):
            self.p.live_preflight(self.ctx)
        self.assertFalse(any('apply' in c for c in self.ctx.calls))
    def test_balanced_disk_quota_subtracts_usage(self):
        self.ctx.ssd_gb=500;self.ctx.ssd_usage=51
        with self.assertRaisesRegex(ValueError,'SSD disk quota'):
            self.p.live_preflight(self.ctx)
    def test_private_tf_copy_omits_provider_cache_and_state(self):
        root=self.ctx.root/'infrastructure/terraform/cluster';root.mkdir(parents=True)
        (root/'versions.tf').write_text('terraform { backend "gcs" {} }')
        (root/'main.tf').write_text('# reviewed')
        (root/'.terraform').mkdir();(root/'.terraform/secret').write_text('private')
        (root/'terraform.tfstate').write_text('secret')
        directory=self.p.terraform_directory(self.ctx,'cluster')
        self.assertEqual((directory/'main.tf').read_text(),'# reviewed')
        self.assertFalse((directory/'.terraform/secret').exists())
        self.assertFalse((directory/'terraform.tfstate').exists())
    def test_refuses_uid_mismatch_before_destroy(self):
        self.ctx.state['resources']=[self.ctx.instance]
        self.ctx.cloud_uid='recreated'
        with self.assertRaises(ValueError):self.t.teardown(self.ctx)
        self.assertFalse(any('destroy' in c or 'delete' in c for c in self.ctx.calls))
    def test_no_foundation_destroy_even_when_cleanup_empty(self):
        self.ctx.state['resources']=[]
        result=self.t.teardown(self.ctx)
        self.assertEqual(result['residual'],[])
        self.assertFalse(any('foundation' in ' '.join(c) and ('destroy' in c or '-destroy' in c) for c in self.ctx.calls))
    def test_explicit_start_records_intent_and_refuses_second_handoff(self):
        self.ctx.state.update(phase='provisioned',watchdog_armed=True,bootstrap_context={'uri':'gs://evidence/control/bootstrap.json','generation':'4'})
        self.ctx.state['terraform']={'controller':{'controller_name':'controller'}}
        self.ctx.state['resources']=[self.ctx.instance]
        self.ctx.state['terraform']['foundation']={'state_bucket':'state-bucket'}
        self.p.acquire_project_lease(self.ctx)
        self.ctx.state['phase']='provisioned'
        self.p.start_controller(self.ctx)
        self.assertTrue(self.ctx.state['runtime_requested'])
        count=len(self.ctx.calls)
        with self.assertRaises(ValueError):self.p.start_controller(self.ctx)
        self.assertEqual(len(self.ctx.calls),count)
    def test_existing_cluster_first_repeat_requires_same_uid(self):
        self.ctx.state['resources']=[{'kind':'cluster','name':'projects/research-project/locations/us-central1-a/clusters/eval','uid':'old'}]
        self.ctx.state['terraform']={'cluster':{'cluster_name':'eval'}}
        self.ctx.cloud_uid='recreated'
        with self.assertRaises(ValueError):self.p.create_cluster(self.ctx,1)
        self.assertFalse(any('apply' in c for c in self.ctx.calls))

class FakeContext:
    def __init__(self,root):
        self.root=root;self.work=root/'private';self.result=root/'results'
        for p in [self.work,self.result]:p.mkdir(parents=True)
        self.manifest_path=root/'manifest.json';self.manifest_path.write_text('{}')
        self.run_id='run-1';self.deadline=time.time()+43000;self.kubeconfig=self.work/'kubeconfig';self.calls=[]
        self.objects={}
        self.config={'gcp':{'project_id':'research-project','billing_account_id':'approved-account','region':'us-central1','zone':'us-central1-a'},'infrastructure':{'kubernetes_version':'1.34.0-gke.1'},'limits':{'infrastructure_seconds':43200}}
        self.state={'owner_id':'gke-qualification-v1','run_id':self.run_id,'started_at':time.time(),'deadline':self.deadline,'resources':[],'terraform':{}}
        self.manifest={'config':self.config,'matrix':[{'id':'fixture-smoke','repeat':1}]};self.billing='approved-account';self.cpus=32;self.ssd_gb=1024;self.ssd_usage=0;self.global_cpus=32;self.global_cpu_usage=0;self.cloud_uid='123'
        self.instance={'kind':'instance','name':'projects/research-project/zones/us-central1-a/instances/controller','uid':'123'}
    def event(self,phase,**kw):self.state.update(phase=phase,**kw)
    def command(self,args,**kwargs):
        args=list(map(str,args));self.calls.append(args);value={};code=0;stderr=''
        joined=' '.join(args)
        if args[:4]==['gcloud','storage','objects','describe']:
            if args[4] in self.objects:value={'generation':'123'}
            else:code=1;stderr='NOT_FOUND'
        elif args[:3]==['gcloud','storage','cat']:value=json.loads(self.objects[args[3].split('#')[0]])
        elif args[:3]==['gcloud','storage','cp']:self.objects[args[4]]=Path(args[3]).read_text()
        elif args[:4]==['gcloud','billing','projects','describe']:value={'billingEnabled':True,'billingAccountName':'billingAccounts/'+self.billing}
        elif args[:3]==['gcloud','projects','describe']:value={'projectId':'research-project','projectNumber':'12345','lifecycleState':'ACTIVE'}
        elif args[:3]==['gcloud','services','list']:value=[{'config':{'name':x+'.googleapis.com'}} for x in ['cloudbilling','compute','container','storage','artifactregistry','run','cloudscheduler','iam','iamcredentials']]
        elif args[:4]==['gcloud','compute','disks','list']:value=[]
        elif args[:4]==['gcloud','compute','regions','describe']:value={'quotas':[{'metric':'CPUS','limit':self.cpus,'usage':0},{'metric':'SSD_TOTAL_GB','limit':self.ssd_gb,'usage':self.ssd_usage}]}
        elif args[:4]==['gcloud','compute','project-info','describe']:value={'quotas':[] if self.global_cpus is None else [{'metric':'CPUS_ALL_REGIONS','limit':self.global_cpus,'usage':self.global_cpu_usage}]}
        elif args[:3]==['gcloud','container','get-server-config']:value={'validMasterVersions':['1.34.0-gke.1'],'validNodeVersions':['1.34.0-gke.1']}
        elif args[:4]==['gcloud','compute','instances','describe'] or args[:4]==['gcloud','container','clusters','describe']:
            value={'id':self.cloud_uid,'labels':{'evaluation-owner':'gke-qualification-v1','evaluation-run':'run-1'},'resourceLabels':{'evaluation-owner':'gke-qualification-v1','evaluation-run':'run-1'}}
        elif 'output' in args:value={}
        return SimpleNamespace(returncode=code,stdout=json.dumps(value),stderr=stderr)

class ProvisionFlowTests(ProvisionTests):
    def test_full_provision_bootstraps_migrates_pins_and_keeps_runtime_dormant(self):
        ctx=CloudContext(self.ctx.root)
        result=self.p.provision(ctx)
        self.assertEqual(result['phase'],'provisioned')
        self.assertFalse(result['runtime_started'])
        self.assertTrue(ctx.state['watchdog_armed'])
        self.assertTrue(ctx.state['bootstrap_context']['generation'].isdigit())
        joined=[' '.join(c) for c in ctx.calls]
        self.assertTrue(any('-migrate-state -force-copy' in c for c in joined))
        self.assertFalse(any('get-credentials' in c for c in joined),'local provision must never contact private Kubernetes endpoint')
        self.assertFalse(any(' reset ' in c for c in joined),'provision never starts runtime')
        self.assertFalse(ctx.state['tfvars']['controller']['runtime_enabled'])
        self.assertNotIn('cluster_endpoint',ctx.state['terraform']['cluster'])
        control=json.loads(ctx.objects[ctx.state['bootstrap_context']['uri']])
        self.assertNotIn('tfvars',control)
        self.assertEqual(control['lease'],ctx.state['lease'])
        self.assertNotIn('cluster_endpoint',control['terraform']['cluster'])
        self.assertTrue((ctx.root/'infrastructure/terraform/foundation/versions.tf').read_text().find('backend "gcs"')>=0)
    def test_one_failed_delete_reports_residual_and_keeps_watchdog(self):
        ctx=CloudContext(self.ctx.root);self.p.provision(ctx)
        ctx.fail_cluster_destroy=True
        result=self.t.teardown(ctx)
        self.assertTrue(result['residual'])
        self.assertTrue(ctx.state['watchdog_armed'])
        self.assertFalse(any('controller' in ' '.join(c) and '-destroy' in c for c in ctx.calls))
        self.assertFalse(any('foundation' in ' '.join(c) and '-destroy' in c for c in ctx.calls))
    def test_clean_teardown_preserves_foundation_and_confirms_absence(self):
        ctx=CloudContext(self.ctx.root);self.p.provision(ctx)
        result=self.t.teardown(ctx)
        self.assertEqual(result['residual'],[])
        self.assertTrue(result['foundation_preserved'])
        self.assertFalse(ctx.exists['cluster']);self.assertFalse(ctx.exists['instance']);self.assertFalse(ctx.exists['router'])
        self.assertFalse(any('foundation' in ' '.join(c) and '-destroy' in c for c in ctx.calls))

class CloudContext(FakeContext):
    def __init__(self,root):
        # ProvisionTests already creates root/private; reconstruct in subdir.
        super().__init__(root/'cloud')
        for name in ['foundation','controller','cluster']:
            path=self.root/'infrastructure/terraform'/name;path.mkdir(parents=True)
            (path/'versions.tf').write_text('terraform {\n backend "gcs" {}\n}\n')
            (path/'main.tf').write_text('# reviewed fixture')
        helper=self.root/'infrastructure/controller/startup.sh';helper.parent.mkdir(parents=True);helper.write_text('#!/bin/sh\n# reviewed fixture\n')
        self.config['infrastructure'].update(controller_image='registry/image@sha256:'+'a'*64,watchdog_image='registry/image@sha256:'+'b'*64,controller_boot_image='projects/research-project/global/images/boot-v1')
        self.objects={};self.exists={kind:False for kind in ['instance','router','cluster','firewall','subnetwork','network']};self.enabled=False;self.fail_cluster_destroy=False;self.plans={}
        self.outputs={'foundation':{'evidence_bucket':'research-project-evidence','state_bucket':'research-project-state','artifact_repository':'registry/repo','controller_service_account':'controller@example','node_service_account':'node@example','watchdog_service_account':'watchdog@example','scheduler_service_account':'scheduler@example'},
        'controller':{'network':'network','subnetwork':'subnetwork','controller_name':'controller','controller_uid':'123','router_resource_name':'projects/research-project/regions/us-central1/routers/eval','watchdog_uri':'https://watchdog.example','watchdog_service_name':'watchdog','scheduler_name':'projects/research-project/locations/us-central1/jobs/watchdog'},
        'cluster':{'cluster_name':'eval','cluster_resource_name':'projects/research-project/locations/us-central1-a/clusters/eval','cluster_endpoint':'PRIVATE-ENDPOINT'}}
        import hashlib
        prefix='karthos-eval-'+hashlib.sha256(b'gke-qualification-v1').hexdigest()[:16]
        cluster='karthos-eval-'+hashlib.sha256(json.dumps(['gke-qualification-v1','run-1'],separators=(',',':')).encode()).hexdigest()[:20]
        self.outputs['controller'].update(controller_name=prefix+'-controller',router_resource_name='projects/research-project/regions/us-central1/routers/'+prefix,
            network_resource_name='projects/research-project/global/networks/'+prefix,subnetwork_resource_name='projects/research-project/regions/us-central1/subnetworks/'+prefix,firewall_resource_name='projects/research-project/global/firewalls/'+prefix+'-internal')
        self.outputs['cluster'].update(cluster_name=cluster,cluster_resource_name='projects/research-project/locations/us-central1-a/clusters/'+cluster)

    def command(self,args,**kwargs):
        args=list(map(str,args));self.calls.append(args);value={};code=0;stderr=''
        if args[0]=='terraform':
            root=Path(args[1].split('=',1)[1]).name
            if 'plan' in args:self.plans[root]={'destroy':'-destroy' in args,'target':any(x.startswith('-target=') for x in args),'targets':[arg.split('=',1)[1] for arg in args if arg.startswith('-target=')]}
            if 'apply' in args:
                plan=self.plans[root]
                if root=='cluster' and plan['destroy'] and self.fail_cluster_destroy:return SimpleNamespace(returncode=1,stdout='',stderr='simulated API failure') if not kwargs.get('check',True) else self._fail()
                if root=='controller' and not (plan['targets'] and all(target.startswith('google_cloud_') for target in plan['targets'])):
                    for kind in ['instance','router','firewall','subnetwork','network']:self.exists[kind]=not plan['destroy']
                if root=='cluster':self.exists['cluster']=not plan['destroy']
            if 'output' in args:
                values=self.outputs[root]
                if root=='controller' and not self.exists['instance']:values={key:v for key,v in values.items() if key in {'watchdog_uri','watchdog_service_name','scheduler_name'}}
                value={key:{'value':v,'sensitive':key=='cluster_endpoint'} for key,v in values.items()}
        elif args[:4]==['gcloud','storage','buckets','describe']:code=1;stderr='NOT_FOUND'
        elif args[:4]==['gcloud','storage','objects','describe']:
            uri=args[4]
            if uri not in self.objects:code=1;stderr='NOT_FOUND'
            else:value={'generation':'123'}
        elif args[:3]==['gcloud','storage','cp']:
            self.objects[args[4]]=Path(args[3]).read_text()
        elif args[:3]==['gcloud','storage','rm']:
            self.objects.pop(args[3],None)
        elif args[:3]==['gcloud','storage','cat']:
            data=self.objects[args[3].split('#')[0]]
            return SimpleNamespace(returncode=0,stdout=data.encode() if kwargs.get('binary') else data,stderr='')
        elif args[:4]==['gcloud','container','clusters','delete']:self.exists['cluster']=False
        elif args[:4]==['gcloud','compute','disks','list']:value=[]
        elif args[:4] in (['gcloud','compute','instances','describe'],['gcloud','compute','routers','describe'],['gcloud','compute','networks','describe'],['gcloud','compute','firewall-rules','describe'],['gcloud','container','clusters','describe']) or args[:5]==['gcloud','compute','networks','subnets','describe']:
            kind='subnetwork' if args[:4]==['gcloud','compute','networks','subnets'] else {'instances':'instance','routers':'router','networks':'network','firewall-rules':'firewall'}.get(args[2],'cluster')
            if not self.exists[kind]:code=1;stderr='NOT_FOUND'
            else:
                labels={'evaluation-owner':'gke-qualification-v1','evaluation-run':'run-1'}
                value={'id':'123','labels':labels,'resourceLabels':labels,'description':json.dumps(labels)}
        elif args[:3]==['gcloud','auth','print-access-token']:return SimpleNamespace(returncode=0,stdout='private-test-caller-token',stderr='')
        elif args[0]=='curl' and args[-1].endswith(':generateIdToken'):value={'token':'private-test-id-token'}
        elif args[0]=='curl':value={'not_hard_guarantee':True,'refused':[],'errors':[],'residual':[],'inventory_generation':self.state['inventory']['generation']}
        elif args[:4]==['gcloud','scheduler','jobs','resume']:self.enabled=True
        elif args[:4]==['gcloud','scheduler','jobs','pause']:self.enabled=False
        elif args[:4]==['gcloud','scheduler','jobs','describe']:
            value={'state':'ENABLED' if self.enabled else 'PAUSED','httpTarget':{'uri':'https://watchdog.example/','oidcToken':{'serviceAccountEmail':'scheduler@example','audience':'https://watchdog.example'}}}
        elif args[:4]==['gcloud','run','services','update']:
            tag=next(arg.split('=',1)[1] for arg in args if arg.startswith('--tag='))
            value={'status':{'traffic':[{'tag':tag,'url':'https://candidate.example','revisionName':'watchdog-revision'}]}}
        elif args[:4]==['gcloud','run','services','update-traffic']:self.serving_revision='watchdog-revision'
        elif args[:4]==['gcloud','run','services','describe']:value={'status':{'traffic':[{'revisionName':getattr(self,'serving_revision','old-revision'),'percent':100}]}}
        else:
            self.calls.pop();return super().command(args,**kwargs)
        return SimpleNamespace(returncode=code,stdout=json.dumps(value),stderr=stderr)
    def _fail(self):raise RuntimeError('simulated API failure')

class EmergencyCleanupTests(ProvisionTests):
    def test_saved_target_cleanup_does_not_require_manifest(self):
        self.assertTrue(hasattr(self.t,'load_cleanup_context'),'saved-inventory cleanup loader missing')
        self.p.freeze_cleanup_target(self.ctx)
        path=self.ctx.root/'.work/gke/run-1/execution.json';path.parent.mkdir(parents=True)
        path.write_text(json.dumps(self.ctx.state))
        emergency=self.t.load_cleanup_context(self.ctx.root,'run-1',executor=lambda *a,**kw: (_ for _ in ()).throw(AssertionError('no cloud calls expected')))
        result=self.t.teardown(emergency)
        self.assertEqual(result['residual'],[])
        with self.assertRaises(ValueError):self.p.provision(emergency)
    def test_missing_saved_target_is_never_manufactured(self):
        self.assertTrue(hasattr(self.t,'load_cleanup_context'),'saved-inventory cleanup loader missing')
        with self.assertRaises(ValueError):self.t.load_cleanup_context(self.ctx.root,'unknown-run')
    def test_tampered_saved_target_refused(self):
        self.assertTrue(hasattr(self.t,'load_cleanup_context'),'saved-inventory cleanup loader missing')
        self.p.freeze_cleanup_target(self.ctx)
        self.ctx.state['cleanup_config']['gcp']['project_id']='other-project'
        path=self.ctx.root/'.work/gke/run-1/execution.json';path.parent.mkdir(parents=True);path.write_text(json.dumps(self.ctx.state))
        with self.assertRaises(ValueError):self.t.load_cleanup_context(self.ctx.root,'run-1')

class FinalCleanupRequestTests(ProvisionTests):
    def test_unverified_summary_never_changes_watchdog(self):
        self.assertTrue(hasattr(self.p,'request_final_cleanup'),'independent final cleanup request missing')
        with self.assertRaises(ValueError):self.p.request_final_cleanup(self.ctx,{'verified':False})
        self.assertEqual(self.ctx.calls,[])
    def test_verified_sealed_summary_pins_early_cleanup_without_self_delete(self):
        import hashlib
        self.assertTrue(hasattr(self.p,'request_final_cleanup'),'independent final cleanup request missing')
        ctx=CloudContext(self.ctx.root)
        self.p.provision(ctx)
        # Add exact cloud network UID inventories required by finalizer.
        ctx.outputs['controller'].update(network_resource_name='projects/research-project/global/networks/eval',subnetwork_resource_name='projects/research-project/regions/us-central1/subnetworks/eval',firewall_resource_name='projects/research-project/global/firewalls/eval')
        ctx.state['terraform']['controller'].update(ctx.outputs['controller'])
        for kind in ['network','subnetwork','firewall']:ctx.exists[kind]=True
        prefix='gs://research-project-evidence/gke-qualification-v1/runs/run-1/final-summary/'
        content=json.dumps({'run_id':'run-1','state':'complete'})
        ctx.objects[prefix+'run-report.json']=content
        ctx.objects[prefix+'GKE-SHA256SUMS']=hashlib.sha256(content.encode()).hexdigest()+'  run-report.json\n'
        result=self.p.request_final_cleanup(ctx,{'verified':True,'uri':prefix})
        self.assertTrue(result['cleanup_requested'])
        self.assertFalse(result['completion_confirmed'])
        inventory=json.loads(ctx.objects[ctx.state['inventory']['uri']])
        self.assertTrue(inventory['completed_export_verified'])
        self.assertIn('network',{r['kind'] for r in inventory['resources']})
        self.assertFalse(any(args[:4]==['gcloud','compute','instances','delete'] for args in ctx.calls))

class ProjectLeaseTests(ProvisionTests):
    def test_another_run_cannot_take_active_project_lease(self):
        self.assertTrue(hasattr(self.p,'acquire_project_lease'),'project lease missing')
        first=CloudContext(self.ctx.root);self.p.provision(first)
        other=CloudContext(self.ctx.root/'other');other.run_id='run-2';other.state['run_id']='run-2'
        other.state['terraform']['foundation']=first.state['terraform']['foundation'];other.objects=first.objects
        with self.assertRaises(ValueError):self.p.acquire_project_lease(other)
        self.assertFalse(any('apply' in args for args in other.calls))
    def test_same_saved_lease_handoffs_and_foreign_generation_refuses_mutation(self):
        self.assertTrue(hasattr(self.p,'assert_project_lease'),'project lease check missing')
        ctx=CloudContext(self.ctx.root);self.p.provision(ctx)
        self.p.assert_project_lease(ctx)
        lease=ctx.state['lease'];stored=json.loads(ctx.objects[lease['uri']]);stored['token']='different-run-token';ctx.objects[lease['uri']]=json.dumps(stored)
        with self.assertRaises(ValueError):self.p.assert_project_lease(ctx)
    def test_cleanup_requested_lease_blocks_recreation(self):
        self.assertTrue(hasattr(self.p,'mark_lease_cleanup'),'project lease cleanup phase missing')
        ctx=CloudContext(self.ctx.root);self.p.provision(ctx)
        self.p.mark_lease_cleanup(ctx)
        with self.assertRaises(ValueError):self.p.assert_project_lease(ctx)
        self.p.assert_project_lease(ctx,cleanup=True)

class TerraformDependencyTests(unittest.TestCase):
    def test_staged_controller_keeps_relative_startup_dependency(self):
        from evaluation.gke.provision import terraform_directory
        with tempfile.TemporaryDirectory() as directory:
            ctx=FakeContext(Path(directory));root=ctx.root/'infrastructure/terraform/controller';root.mkdir(parents=True)
            (root/'main.tf').write_text('metadata_startup_script = file("${path.module}/../../controller/startup.sh")')
            helper=ctx.root/'infrastructure/controller/startup.sh';helper.parent.mkdir(parents=True);helper.write_text('#!/bin/sh\n# reviewed bytes\n')
            staged=terraform_directory(ctx,'controller');target=staged/'../../controller/startup.sh'
            self.assertTrue(target.is_file());self.assertEqual(target.read_bytes(),helper.read_bytes())

class WatchdogContinuityTests(ProvisionTests):
    def test_refresh_failure_never_pauses_or_dryruns_production(self):
        ctx=CloudContext(self.ctx.root);self.p.provision(ctx)
        ctx.calls.clear();original=ctx.command
        def fail_update(args,**kwargs):
            if list(args)[:4]==['gcloud','run','services','update']:raise RuntimeError('controller died during replacement')
            return original(args,**kwargs)
        ctx.command=fail_update
        with self.assertRaises(RuntimeError):self.p.arm_watchdog(ctx)
        self.assertTrue(ctx.enabled,'existing independent Scheduler must remain enabled')
        self.assertTrue(ctx.state['watchdog_armed'])
        self.assertFalse(any(args[:4]==['gcloud','scheduler','jobs','pause'] for args in ctx.calls))
        self.assertFalse(any('DRY_RUN=true' in arg for args in ctx.calls for arg in args))
    def test_cluster_creation_intent_is_active_before_blocking_apply(self):
        ctx=CloudContext(self.ctx.root);original=ctx.command;observed=[]
        def inspect_apply(args,**kwargs):
            args=list(map(str,args))
            if args[0]=='terraform' and args[1].endswith('/cluster') and 'apply' in args:
                inventory=json.loads(ctx.objects[ctx.state['inventory']['uri']])
                observed.append(inventory.get('creation_intents',[]))
                self.assertTrue(ctx.enabled)
            return original(args,**kwargs)
        ctx.command=inspect_apply;self.p.provision(ctx)
        self.assertTrue(observed and observed[0], 'accepted API creation must already be named in independent inventory')
        self.assertEqual(observed[0][0]['kind'],'cluster')
    def test_accepted_apply_timeout_retains_active_intent_and_cleanup_recovers_uid(self):
        ctx=CloudContext(self.ctx.root);original=ctx.command
        def timeout_after_accept(args,**kwargs):
            args=list(map(str,args))
            if args[0]=='terraform' and args[1].endswith('/cluster') and 'apply' in args:
                ctx.exists['cluster']=True
                raise TimeoutError('accepted API response lost')
            return original(args,**kwargs)
        ctx.command=timeout_after_accept
        with self.assertRaises(TimeoutError):self.p.provision(ctx)
        self.assertTrue(ctx.enabled)
        self.assertTrue(ctx.state['creation_intents'])
        pinned=json.loads(ctx.objects[ctx.state['inventory']['uri']])
        self.assertTrue(pinned['creation_intents'])
        ctx.command=original
        result=self.t.destroy_cluster(ctx)
        self.assertEqual(result['residual'],[])
        self.assertFalse(ctx.exists['cluster'])
        self.assertTrue(ctx.enabled,'cleanup of accepted creation must preserve independent deadline')
        self.assertTrue(any('/uid-bindings/' in uri for uri in ctx.objects))
    def test_cluster_delete_refreshes_watchdog_with_cleanup_allowance(self):
        from unittest.mock import patch
        ctx=CloudContext(self.ctx.root);self.p.provision(ctx)
        original=self.t.arm_watchdog
        def reserve_expired(context, **kwargs):
            if kwargs.get('cleanup') is not True:
                raise TimeoutError('readiness traffic reserve reached')
            return original(context, **kwargs)
        with patch.object(self.t,'arm_watchdog',side_effect=reserve_expired) as arm:
            result=self.t.destroy_cluster(ctx)
        self.assertEqual(result['residual'],[])
        self.assertFalse(ctx.exists['cluster'])
        self.assertTrue(ctx.enabled)
        self.assertTrue(all(call.kwargs.get('cleanup') is True for call in arm.call_args_list))

    def test_first_compute_apply_requires_already_verified_external_finalizer(self):
        ctx=CloudContext(self.ctx.root);original=ctx.command;compute_applies=[]
        def inspect_apply(args,**kwargs):
            args=list(map(str,args))
            if args[0]=='terraform' and args[1].endswith('/controller') and 'apply' in args:
                plan=ctx.plans['controller']
                if not plan['targets']:
                    compute_applies.append(True)
                    self.assertTrue(ctx.state['watchdog_armed'])
                    self.assertTrue(ctx.enabled)
                    self.assertFalse(ctx.exists['instance'])
            return original(args,**kwargs)
        ctx.command=inspect_apply;self.p.provision(ctx)
        self.assertTrue(compute_applies)
    def test_candidate_dryrun_failure_preserves_old_serving_revision(self):
        ctx=CloudContext(self.ctx.root);self.p.provision(ctx);ctx.serving_revision='old-active-revision'
        original=ctx.command;ctx.calls.clear()
        def fail_dryrun(args,**kwargs):
            if list(args)[0]=='curl':
                ctx.calls.append(list(args));return SimpleNamespace(returncode=0,stdout=json.dumps({'not_hard_guarantee':False}),stderr='')
            return original(args,**kwargs)
        ctx.command=fail_dryrun
        with self.assertRaises(ValueError):self.p.arm_watchdog(ctx)
        self.assertEqual(ctx.serving_revision,'old-active-revision')
        self.assertTrue(ctx.enabled)
        self.assertFalse(any(args[:4]==['gcloud','run','services','update-traffic'] for args in ctx.calls))
        self.assertFalse(any(args[:4]==['gcloud','scheduler','jobs','pause'] for args in ctx.calls))

    def test_first_watchdog_gate_retries_temporary_forbidden_before_compute(self):
        from unittest.mock import patch
        ctx=CloudContext(self.ctx.root)
        original=ctx.command
        attempts=[]
        def delayed_invoker(args,**kwargs):
            args=list(args)
            if args[0]=='curl' and 'X-Karthos-Dry-Run: true' in args:
                attempts.append(args)
                if len(attempts)==1:
                    self.assertFalse(ctx.exists['instance'])
                    return SimpleNamespace(returncode=22,stdout='',stderr='curl: (22) The requested URL returned error: 403\n')
            return original(args,**kwargs)
        ctx.command=delayed_invoker
        with patch.object(self.p.time,'sleep') as sleep:
            self.p.provision(ctx)
        self.assertEqual(attempts[0],attempts[1])
        sleep.assert_called_once_with(20)
        self.assertTrue(ctx.state['watchdog_armed'])

    def test_watchdog_forbidden_retries_are_bounded_and_never_create_compute(self):
        from unittest.mock import patch
        for status,expected in ((403,7),(500,1)):
            with self.subTest(status=status):
                ctx=CloudContext(self.ctx.root/str(status))
                original=ctx.command
                attempts=[]
                def denied(args,**kwargs):
                    if args[0]=='curl' and 'X-Karthos-Dry-Run: true' in args:
                        attempts.append(args)
                        return SimpleNamespace(returncode=22,stdout='',stderr=f'curl: (22) The requested URL returned error: {status}\n')
                    return original(args,**kwargs)
                ctx.command=denied
                with patch.object(self.p.time,'sleep'), self.assertRaisesRegex(RuntimeError,str(status)):
                    self.p.provision(ctx)
                self.assertEqual(len(attempts),expected)
                self.assertFalse(ctx.exists['instance'])
                self.assertFalse(ctx.state['watchdog_armed'])
