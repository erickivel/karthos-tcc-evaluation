"""Explicit-target GCP orchestration; importing performs no commands.

All state/plan/tfvars bytes stay in private ctx.work. Events contain only phase
and coarse counts. This module never creates a project or billing account.
"""
from __future__ import annotations
from .models import CAPTURE_V2_SCHEDULES, RECOVERABLE_SCHEDULES, COMPLETION_V2
import hashlib,json,os,re,shutil,time,uuid
from pathlib import Path
from datetime import datetime,timezone
from .models import read,write,identifier,canonical_sha,sha

OWNER='gke-qualification-v1'
COMPUTE_CLI_PATHS={'instance':('instances',),'disk':('disks',),'router':('routers',),'network':('networks',),'subnetwork':('networks','subnets'),'firewall':('firewall-rules',)}
APIS={'cloudresourcemanager.googleapis.com','logging.googleapis.com','cloudbilling.googleapis.com','compute.googleapis.com','container.googleapis.com','storage.googleapis.com',
      'artifactregistry.googleapis.com','run.googleapis.com','cloudscheduler.googleapis.com','iam.googleapis.com','iamcredentials.googleapis.com'}
OUTPUTS={
 'foundation':{'quarantine_bucket','backup_bucket','evidence_bucket','state_bucket','artifact_repository','controller_service_account','node_service_account','watchdog_service_account','scheduler_service_account'},
 'controller':{'network','subnetwork','controller_name','controller_uid','watchdog_uri','router_resource_name','watchdog_service_name','scheduler_name','network_resource_name','subnetwork_resource_name','firewall_resource_name'},
 'cluster':{'cluster_name','cluster_resource_name','node_pools'},
}


def require_context(ctx,*,cleanup=False):
    if getattr(ctx,'cleanup_only',False) and not cleanup:
        raise ValueError('saved-inventory context authorizes cleanup only')
    identifier(ctx.run_id)
    if ctx.state.get('owner_id') != OWNER:
        raise ValueError('unexpected evaluation ownership')
    if ctx.state.get('run_id') != ctx.run_id:
        raise ValueError('run identity mismatch')
    if not getattr(ctx,'cleanup_only',False) and not 0 < ctx.state['deadline']-ctx.state['started_at'] <= min(64800,ctx.config.get('limits',{}).get('infrastructure_seconds',43200)):
        raise ValueError('invalid approved infrastructure deadline')


def doc(ctx,args,**kwargs):
    value=ctx.command(args,**kwargs)
    return json.loads(value.stdout or '{}')


def not_found(result):
    text=result.stderr.decode(errors='replace') if isinstance(result.stderr,bytes) else result.stderr
    return bool(re.search(r'NOT_FOUND|HTTPError\s+404|HTTP\s+404|was not found|ResponseError:\s+code=404,\s+message=Not found:|\bnot found:\s*404(?:\.|\s|$)', text or ''))


LEASE_OBJECT='control/active-run.json'


def _lease_read(ctx,*,cleanup=False):
    lease=ctx.state.get('lease')
    if not lease or lease.get('object')!=LEASE_OBJECT or lease.get('bucket')!=ctx.state['terraform']['foundation']['state_bucket']:
        raise ValueError('exact saved project lease required')
    metadata=doc(ctx,['gcloud','storage','objects','describe',lease['uri'],'--format=json'],cleanup=cleanup)
    generation=str(metadata.get('generation',''))
    if not generation.isdigit():raise ValueError('project lease generation missing')
    value=doc(ctx,['gcloud','storage','cat',lease['uri']+'#'+generation],cleanup=cleanup)
    expected={'project_id':ctx.config['gcp']['project_id'],'owner_id':OWNER,'run_id':ctx.run_id,'token':lease['token']}
    if any(value.get(key)!=item for key,item in expected.items()):raise ValueError('another run owns project lease')
    return generation,value


def acquire_project_lease(ctx):
    require_context(ctx)
    bucket=ctx.state['terraform']['foundation']['state_bucket'];uri='gs://'+bucket+'/'+LEASE_OBJECT
    observation=ctx.command(['gcloud','storage','objects','describe',uri,'--format=json'],check=False)
    if observation.returncode==0:
        if not ctx.state.get('lease'):raise ValueError('project already has an active run; lease takeover forbidden')
        assert_project_lease(ctx)
        return ctx.state['lease']
    if not not_found(observation):raise RuntimeError('cannot establish project lease absence')
    token=uuid.uuid4().hex
    lease={'bucket':bucket,'object':LEASE_OBJECT,'uri':uri,'token':token}
    ctx.state['lease']=lease;ctx.event('project-lease-acquire-intent')
    value={'schema_version':1,'project_id':ctx.config['gcp']['project_id'],'owner_id':OWNER,'run_id':ctx.run_id,'token':token,'phase':'active','acquired_at':time.time(),'deadline':ctx.deadline}
    path=ctx.work/'project-lease.json';write(path,value);os.chmod(path,0o600)
    ctx.command(['gcloud','storage','cp',str(path),uri,'--if-generation-match=0'])
    generation,observed=_lease_read(ctx);lease['generation']=generation
    ctx.event('project-lease-acquired')
    return lease


def assert_project_lease(ctx,*,cleanup=False):
    generation,value=_lease_read(ctx,cleanup=cleanup)
    if generation!=str(ctx.state['lease'].get('generation')):
        if not cleanup:raise ValueError('project lease generation changed; inspect owner before mutation')
        ctx.state['lease']['generation']=generation
    if value.get('phase') not in ({'active','cleanup-requested'} if cleanup else {'active'}):
        raise ValueError('project lease is finalizing; new mutations refused')
    return value


def mark_lease_cleanup(ctx):
    value=assert_project_lease(ctx,cleanup=True)
    if value.get('phase')=='cleanup-requested':return
    value.update(phase='cleanup-requested',cleanup_requested_at=time.time())
    path=ctx.work/'project-lease-finalizing.json';write(path,value);os.chmod(path,0o600)
    ctx.command(['gcloud','storage','cp',str(path),ctx.state['lease']['uri'],'--if-generation-match='+ctx.state['lease']['generation']],cleanup=True)
    generation,_=_lease_read(ctx,cleanup=True);ctx.state['lease']['generation']=generation
    ctx.event('project-lease-finalizing')


def release_project_lease(ctx,*,confirmed_absent=False):
    if not confirmed_absent:raise ValueError('confirmed owned-resource absence required before releasing lease')
    assert_project_lease(ctx,cleanup=True)
    lease=ctx.state['lease']
    ctx.command(['gcloud','storage','rm',lease['uri'],'--if-generation-match='+lease['generation']],cleanup=True)
    ctx.state['project_lease_released']=True;ctx.event('project-lease-released')


def refuse_foreign_controller(ctx):
    name='karthos-eval-'+hashlib.sha256(OWNER.encode()).hexdigest()[:16]+'-controller'
    resource={'kind':'instance','name':'projects/'+ctx.config['gcp']['project_id']+'/zones/'+ctx.config['gcp']['zone']+'/instances/'+name}
    actual=cloud_get(ctx,resource)
    if actual is not None and (actual['owner_id']!=OWNER or actual['run_id']!=ctx.run_id):
        raise ValueError('deterministic controller name is owned by another run')
    for previous in ctx.state.get('resources',[]):
        if previous['name']==resource['name'] and actual is not None and str(previous['uid'])!=actual['uid']:
            raise ValueError('controller UID changed without explicit inspected recovery')


def live_preflight(ctx):
    require_context(ctx)
    config=ctx.config;g=config['gcp'];project=g['project_id']
    if ctx.manifest.get('schedule') in CAPTURE_V2_SCHEDULES:
        from .cloud_identity import principal_preflight
        ctx.write(ctx.work/'principal-preflight.json',principal_preflight(ctx))
    if not project or not g.get('billing_account_id'):
        raise ValueError('dedicated project and billing inputs required')
    actual=doc(ctx,['gcloud','projects','describe',project,'--format=json'])
    if actual.get('projectId')!=project or actual.get('lifecycleState')!='ACTIVE':
        raise ValueError('supplied dedicated project is not active')
    billing=doc(ctx,['gcloud','billing','projects','describe',project,'--billing-project='+project,'--format=json'])
    if not billing.get('billingEnabled') or billing.get('billingAccountName')!='billingAccounts/'+g['billing_account_id'].removeprefix('billingAccounts/'):
        raise ValueError('actual billing account does not match reviewed input')
    enabled=doc(ctx,['gcloud','services','list','--enabled','--project='+project,'--format=json'])
    names={item.get('config',{}).get('name') for item in enabled}
    if not APIS <= names:
        raise ValueError('required cloud APIs are not already enabled: '+', '.join(sorted(APIS-names)))
    region=doc(ctx,['gcloud','compute','regions','describe',g['region'],'--project='+project,'--format=json'])
    quotas={q['metric']:float(q['limit'])-float(q.get('usage',0)) for q in region.get('quotas',[])}
    # Includes transient default GKE node pool while the three fixed pools start.
    if quotas.get('CPUS',0)<20 or ('E2_CPUS' in quotas and quotas['E2_CPUS']<20):
        raise ValueError('actual available CPU quota is below conservative bootstrap requirement')
    # Balanced disks consume SSD quota: controller 300 GiB + 3 workers at 50 GiB.
    # Necessary boot-disk floor only; PVC/bootstrap capacity remains a readiness gate.
    if quotas.get('SSD_TOTAL_GB',0)<450:
        raise ValueError('actual available SSD disk quota is below boot-disk floor (450 GiB); additional PVC/bootstrap quota is also required')
    project_info=doc(ctx,['gcloud','compute','project-info','describe','--project='+project,'--format=json'])
    global_quotas={q['metric']:float(q['limit'])-float(q.get('usage',0)) for q in project_info.get('quotas',[])}
    # Some projects also have a global CPU cap independent of regional quotas.
    if 'CPUS_ALL_REGIONS' in global_quotas and global_quotas['CPUS_ALL_REGIONS']<20:
        raise ValueError('actual available global CPU quota is below conservative bootstrap requirement (20 vCPUs)')
    versions=doc(ctx,['gcloud','container','get-server-config','--zone='+g['zone'],'--project='+project,'--format=json'])
    version=config['infrastructure']['kubernetes_version']
    channel=config['infrastructure'].get('release_channel')
    if channel and not any(c.get('channel')==channel and version in c.get('validVersions',[]) for c in versions.get('channels',[])):
        raise ValueError('frozen exact GKE version unavailable in selected release channel')
    if version not in versions.get('validMasterVersions',[]) or version not in versions.get('validNodeVersions',[]):
        raise ValueError('reviewed exact GKE version unavailable')
    ctx.state['project_number']=str(actual['projectNumber'])
    ctx.event('live-preflight-verified')


def terraform_directory(ctx,root):
    if root not in OUTPUTS:raise ValueError('unknown Terraform root')
    destination=ctx.work/'terraform'/root
    if not destination.exists():
        source=ctx.root/'infrastructure/terraform'/root
        if not source.is_dir():raise ValueError('reviewed Terraform source missing')
        if any(path.is_symlink() for path in source.rglob('*')):raise ValueError('Terraform source symlinks forbidden')
        shutil.copytree(source,destination,ignore=shutil.ignore_patterns('.terraform','*.tfstate','*.tfstate.*','*.tfplan','*.tfvars','*.tfvars.json'))
        os.chmod(destination,0o700)
    if root=='controller':
        # main.tf keeps its reviewed ../../controller/startup.sh handoff.
        source=ctx.root/'infrastructure/controller/startup.sh'
        target=ctx.work/'controller/startup.sh'
        if not source.is_file() or source.is_symlink():raise ValueError('reviewed controller startup dependency missing or unsafe')
        target.parent.mkdir(parents=True,exist_ok=True);os.chmod(target.parent,0o700)
        if target.is_symlink() or (target.exists() and target.read_bytes()!=source.read_bytes()):raise ValueError('staged startup dependency changed; inspect saved run')
        if not target.exists():shutil.copyfile(source,target);target.chmod(0o700)
    return destination


def tf_init(ctx,root,*,bootstrap=False,migrate=False,cleanup=False):
    directory=terraform_directory(ctx,root)
    args=['terraform','-chdir='+str(directory),'init','-input=false','-lockfile=readonly']
    if bootstrap:
        args+=['-backend=false']
    else:
        bucket=ctx.state.get('terraform',{}).get('foundation',{}).get('state_bucket') or ctx.config['gcp'].get('state_bucket_name') or ctx.config['gcp']['project_id']+'-karthos-eval-state'
        prefix=ctx.config['gcp'].get('foundation_reference',{}).get('prefix','karthos-eval/foundation') if root=='foundation' else 'karthos-eval/'+root
        args+=['-backend-config=bucket='+bucket,'-backend-config=prefix='+prefix]
        if migrate:args+=['-migrate-state','-force-copy']
    ctx.command(args,timeout=600,cleanup=cleanup)
    return directory


def tf_apply(ctx,root,variables,*,bootstrap=False,destroy=False,targets=(),cleanup=False):
    require_context(ctx,cleanup=cleanup and destroy)
    if root!='foundation':assert_project_lease(ctx,cleanup=cleanup)
    if root=='controller' and not destroy:refuse_foreign_controller(ctx)
    if destroy and root=='foundation':raise ValueError('foundation destruction forbidden')
    directory=terraform_directory(ctx,root)
    variable_file=directory/'approved.auto.tfvars.json';write(variable_file,variables);os.chmod(variable_file,0o600)
    plan=directory/'approved.tfplan'
    args=['terraform','-chdir='+str(directory),'plan','-input=false','-lock=true','-out='+str(plan),'-var-file='+str(variable_file)]
    if destroy:args+=['-destroy']
    args+=['-target='+target for target in targets]
    ctx.command(args,timeout=1200,cleanup=cleanup)
    if root!='foundation':assert_project_lease(ctx,cleanup=cleanup)
    ctx.command(['terraform','-chdir='+str(directory),'apply','-input=false','-lock=true',str(plan)],timeout=2400,cleanup=cleanup)
    if not destroy:
        raw=doc(ctx,['terraform','-chdir='+str(directory),'output','-json'],cleanup=cleanup)
        sanitized={key:item['value'] for key,item in raw.items() if key in OUTPUTS[root] and not item.get('sensitive') and item.get('value') is not None}
        ctx.state.setdefault('terraform',{})[root]=sanitized
        ctx.state.setdefault('tfvars',{})[root]=variables
        ctx.event(root+'-applied')
        return sanitized
    ctx.event(root+'-destroy-applied')


def _base(ctx):
    return {'project_id':ctx.config['gcp']['project_id'],'region':ctx.config['gcp']['region']}


def cluster_variables(ctx):
    g=ctx.config['gcp'];out=ctx.state['terraform'];controller=out['controller']
    return _base(ctx)|{'zone':g['zone'],'owner_id':OWNER,'run_id':ctx.run_id,
      'network':controller['network'],'subnetwork':controller['subnetwork'],
      'node_service_account':out['foundation']['node_service_account'],
      'kubernetes_version':ctx.config['infrastructure']['kubernetes_version'],
      'maintenance_start_time':datetime.fromtimestamp(ctx.state['started_at'],timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
      'maintenance_end_time':datetime.fromtimestamp(ctx.state['deadline'],timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}


def _controller_vars(ctx):
    foundation=ctx.state['terraform']['foundation'];g=ctx.config['gcp'];i=ctx.config['infrastructure']
    manifest=ctx.state['manifest_object'];inventory=ctx.state.get('inventory',{'object':'control/unarmed.json','generation':'0'})
    boot=ctx.state.get('bootstrap_context',{'uri':'','generation':''})
    return _base(ctx)|{'zone':g['zone'],'owner_id':OWNER,'run_id':ctx.run_id,
      **{name:foundation[name] for name in ['evidence_bucket','controller_service_account','watchdog_service_account','scheduler_service_account']},
      'controller_image':i['controller_image'],'bootstrap_sdk_image':i.get('bootstrap_sdk_image',''),'watchdog_image':i['watchdog_image'],'controller_boot_image':i['controller_boot_image'],
      'manifest_uri':manifest['uri'],'manifest_generation':manifest['generation'],'manifest_sha256':manifest['sha256'],
      'inventory_object':inventory['object'],'inventory_generation':inventory['generation'],
      'watchdog_enabled':bool(ctx.state.get('watchdog_armed',False) or ctx.state.get('watchdog_requested',False)),'state_bucket':foundation['state_bucket'],
      'runtime_enabled':bool(ctx.state.get('runtime_requested',False)),
      'bootstrap_context_uri':boot['uri'],'bootstrap_context_generation':boot['generation']}


def upload_file(ctx,path,object_name,*,cleanup=False):
    bucket=ctx.state['terraform']['foundation']['evidence_bucket'];uri='gs://'+bucket+'/'+object_name
    # Create-only objects; generations and bytes are verified after the write.
    existing=ctx.command(['gcloud','storage','objects','describe',uri,'--format=json'],check=False,cleanup=cleanup)
    if existing.returncode:
        if not not_found(existing):raise RuntimeError('GCS object observation failed; cannot assume absence')
        ctx.command(['gcloud','storage','cp',str(path),uri,'--if-generation-match=0'],timeout=600,cleanup=cleanup)
    metadata=doc(ctx,['gcloud','storage','objects','describe',uri,'--format=json'],cleanup=cleanup)
    generation=str(metadata.get('generation',''))
    if not generation.isdigit():raise ValueError('GCS object generation missing')
    content=ctx.command(['gcloud','storage','cat',uri+'#'+generation],binary=True,cleanup=cleanup).stdout
    if isinstance(content,str):content=content.encode()
    if hashlib.sha256(content).hexdigest()!=hashlib.sha256(Path(path).read_bytes()).hexdigest():
        raise ValueError('immutable GCS object differs from reviewed bytes')
    return {'uri':uri,'object':object_name,'generation':generation}


def freeze_cleanup_target(ctx):
    # Private recovery data contains no credentials and is never exported.
    config=json.loads(json.dumps(ctx.config))
    identity={key:config['gcp'][key] for key in ['project_id','region','zone']}
    identity.update(owner_id=OWNER,run_id=ctx.run_id)
    ctx.state['cleanup_config']=config
    ctx.state['cleanup_config_sha256']=canonical_sha(config)
    ctx.state['cleanup_target']=identity
    ctx.state['cleanup_target_sha256']=canonical_sha(identity)
    ctx.event('cleanup-target-frozen')


def foundation_variables(ctx):
    g=ctx.config['gcp']
    values=_base(ctx)|{'state_bucket_name':g.get('state_bucket_name') or g['project_id']+'-karthos-eval-state'}
    if g.get('operator_member'):values['operator_member']=g['operator_member']
    return values


def foundation_source_hashes(ctx):
    root=ctx.root/'infrastructure/terraform/foundation'
    files=[p for p in root.iterdir() if p.suffix=='.tf' or p.name=='.terraform.lock.hcl']
    if any(p.is_symlink() for p in files):raise ValueError('foundation source symlink forbidden')
    return {p.name:sha(p) for p in sorted(files) if p.is_file()}


def reuse_foundation(ctx):
    """Read/plan an explicitly identified foundation; never import or apply it."""
    require_context(ctx)
    g=ctx.config['gcp'];ref=g.get('foundation_reference',{})
    bucket=g.get('state_bucket_name') or g['project_id']+'-karthos-eval-state'
    if ref.get('project_id')!=g['project_id'] or ref.get('state_bucket')!=bucket:
        raise ValueError('foundation reference project/bucket mismatch')
    if ref.get('prefix') not in ('foundation','karthos-eval/foundation') or not re.fullmatch(r'[a-f0-9-]{36}',str(ref.get('lineage',''))):
        raise ValueError('invalid explicit foundation prefix/lineage')
    if ref.get('source_files')!=foundation_source_hashes(ctx):raise ValueError('foundation source differs from prepared reference')
    expected=ref.get('expected_outputs',{})
    required=OUTPUTS['foundation'] if ctx.manifest.get('schedule') in CAPTURE_V2_SCHEDULES else OUTPUTS['foundation']-{'quarantine_bucket'}
    if not required<=set(expected) or set(expected)-OUTPUTS['foundation'] or expected.get('state_bucket')!=bucket:
        raise ValueError('complete foundation output identity required')
    members=doc(ctx,['gcloud','storage','buckets','list','--project='+g['project_id'],'--format=json'])
    if not any(b.get('name')==bucket or str(b.get('storage_url','')).rstrip('/')=='gs://'+bucket for b in members):
        raise ValueError('foundation state bucket is not in target project')
    metadata=doc(ctx,['gcloud','storage','buckets','describe','gs://'+bucket,'--format=json'])
    if metadata.get('public_access_prevention')!='enforced' or not metadata.get('uniform_bucket_level_access') or not metadata.get('versioning_enabled'):
        raise ValueError('foundation state bucket protections missing')
    directory=tf_init(ctx,'foundation')
    state=doc(ctx,['terraform','-chdir='+str(directory),'state','pull'])
    if state.get('lineage')!=ref['lineage']:raise ValueError('foundation state lineage mismatch')
    variables=foundation_variables(ctx);path=directory/'approved.auto.tfvars.json';write(path,variables);os.chmod(path,0o600)
    result=ctx.command(['terraform','-chdir='+str(directory),'plan','-input=false','-lock=true','-detailed-exitcode','-var-file='+str(path)],check=False,timeout=1200)
    if result.returncode!=0:raise ValueError('foundation reuse rejected: plan has changes or failed; inspect private plan output')
    raw=doc(ctx,['terraform','-chdir='+str(directory),'output','-json'])
    outputs={key:item['value'] for key,item in raw.items() if key in OUTPUTS['foundation'] and not item.get('sensitive')}
    if {k:outputs.get(k) for k in expected}!=expected or (ctx.manifest.get('schedule') in CAPTURE_V2_SCHEDULES and 'quarantine_bucket' not in outputs):raise ValueError('foundation output identity mismatch')
    ctx.state.setdefault('terraform',{})['foundation']=outputs
    ctx.state.setdefault('tfvars',{})['foundation']=variables
    ctx.event('foundation-reused',foundation_lineage=ref['lineage'],foundation_prefix=ref['prefix'])
    return outputs


def provision(ctx):
    require_context(ctx)
    if ctx.state.get('terraform'):
        reference=ctx.config['gcp'].get('foundation_reference',{})
        # Only an already verified, read-only foundation checkpoint can retry.
        # Reuse revalidates remote state; no lease, intent, or runtime may exist.
        retry=(ctx.state.get('phase')=='foundation-reused'
               and set(ctx.state['terraform'])=={'foundation'}
               and ctx.state['terraform']['foundation']==reference.get('expected_outputs')
               and not any(ctx.state.get(k) for k in ('lease','resources','creation_intents','pending_resources','watchdog_requested','watchdog_armed','runtime_requested')))
        if not retry:raise ValueError('existing provisioning state; inspect status rather than blindly retry bootstrap')
    freeze_cleanup_target(ctx)
    live_preflight(ctx)
    directory=terraform_directory(ctx,'foundation');versions=directory/'versions.tf';original=versions.read_text()
    # Only this private working copy loses backend block during explicit bootstrap.
    modified,count=re.subn(r'backend\s+"gcs"\s*\{\s*\}','',original)
    if count!=1:raise ValueError('unexpected reviewed backend declaration')
    variables=foundation_variables(ctx)
    exists=ctx.command(['gcloud','storage','buckets','describe','gs://'+variables['state_bucket_name'],'--format=json'],check=False)
    if ctx.config['gcp'].get('foundation_reference'):
        if exists.returncode:raise ValueError('referenced foundation bucket is unavailable; bootstrap is forbidden')
        reuse_foundation(ctx)
    else:
        if exists.returncode==0:
            raise ValueError('state bucket already exists; explicit reviewed import/recovery required before first bootstrap')
        if not not_found(exists):raise RuntimeError('state bucket observation failed; cannot assume absence')
        ctx.event('foundation-bootstrap-intent')
        try:
            versions.write_text(modified);tf_init(ctx,'foundation',bootstrap=True)
            tf_apply(ctx,'foundation',variables,bootstrap=True)
        finally:versions.write_text(original)
        tf_init(ctx,'foundation',migrate=True)
        ctx.event('foundation-remote-state-migrated')
    from .quarantine import verify_preprovision_policy
    verify_preprovision_policy(ctx)
    acquire_project_lease(ctx)
    digest=hashlib.sha256(ctx.manifest_path.read_bytes()).hexdigest()
    ctx.state['manifest_object']=dict(upload_file(ctx,ctx.manifest_path,'control/'+ctx.run_id+'/manifests/'+digest+'.json'),sha256=digest)
    # Verify the external deadline watcher before creating private networking or a dormant controller VM.
    ctx.state['watchdog_armed']=False
    prefix='karthos-eval-'+hashlib.sha256(OWNER.encode()).hexdigest()[:16]
    project=ctx.config['gcp']['project_id'];zone=ctx.config['gcp']['zone'];region=ctx.config['gcp']['region']
    register_creation_intents(ctx,[{'kind':'instance','name':f'projects/{project}/zones/{zone}/instances/{prefix}-controller'},
      {'kind':'router','name':f'projects/{project}/regions/{region}/routers/{prefix}'},
      {'kind':'firewall','name':f'projects/{project}/global/firewalls/{prefix}-internal'},
      {'kind':'subnetwork','name':f'projects/{project}/regions/{region}/subnetworks/{prefix}'},
      {'kind':'network','name':f'projects/{project}/global/networks/{prefix}'}])
    ctx.state['watchdog_requested']=True
    tf_init(ctx,'controller')
    # External finalizer has no dependency on the private network or VM. Establish
    # and verify it first so accepted VM/network creation is never unarmed.
    tf_apply(ctx,'controller',_controller_vars(ctx),targets=['google_cloud_run_v2_service.watchdog','google_cloud_run_v2_service_iam_member.scheduler','google_cloud_scheduler_job.watchdog'])
    arm_watchdog(ctx,refresh=False)
    tf_apply(ctx,'controller',_controller_vars(ctx))
    # Arm VM/NAT ownership first; cluster creation must not leave a completely
    # dormant deadline mechanism if its API call fails.
    arm_watchdog(ctx)
    continuation=ctx.manifest.get('continuation') or {}
    completed={r['id'] for r in continuation.get('records',[]) if r.get('retry_policy_complete')}
    pending=[r for r in ctx.manifest['matrix'] if r['id'] not in completed]
    first_repeat=pending[0]['repeat'] if pending else 1
    if first_repeat==2 and not ctx.state.get('cluster_generation'):ctx.state['cluster_generation']=1
    create_cluster(ctx,first_repeat,credentials=False)
    publish_bootstrap_context(ctx)
    ctx.event('provisioned')
    return {'phase':'provisioned','watchdog_armed':True,'runtime_started':False}


def cloud_get(ctx,resource,*,cleanup=False):
    project=ctx.config['gcp']['project_id'];name=resource['name'];parts=name.split('/')
    segment={'cluster':'clusters','instance':'instances','disk':'disks','router':'routers','network':'networks','subnetwork':'subnetworks','firewall':'firewalls'}.get(resource.get('kind'))
    if not segment or not re.fullmatch(r'projects/'+re.escape(project)+r'/(?:((?:zones|locations|regions)/[a-z0-9-]+)|global)/'+segment+r'/[a-z0-9-]+',name):raise ValueError('cross-project or noncanonical resource refused')
    kind=resource['kind'];location=parts[3];short=parts[-1]
    if kind=='cluster':args=['gcloud','container','clusters','describe',short,'--zone='+location]
    elif kind in {'instance','disk','router','network','subnetwork','firewall'}:
        args=['gcloud','compute',*COMPUTE_CLI_PATHS[kind],'describe',short]
        if parts[2]!='global':args.append(('--region=' if parts[2]=='regions' else '--zone=')+location)
    else:raise ValueError('unsupported cleanup resource kind')
    response=ctx.command(args+['--project='+project,'--format=json'],check=False,cleanup=cleanup)
    if response.returncode:
        # A permission/transport error must never be interpreted as resource absence.
        if not_found(response):return None
        raise RuntimeError('cloud resource observation failed')
    value=json.loads(response.stdout)
    labels=value.get('resourceLabels',value.get('labels',{}))
    if kind in {'router','network','subnetwork','firewall'}:
        try:labels=json.loads(value.get('description','{}'))
        except ValueError:labels={}
    return {'uid':str(value.get('id','')),'owner_id':labels.get('evaluation-owner'),'run_id':labels.get('evaluation-run'),'value':value}


def verify_resource(ctx,resource,*,cleanup=False):
    actual=cloud_get(ctx,resource,cleanup=cleanup)
    if actual is None:return None
    if actual['uid']!=str(resource['uid']) or actual['owner_id']!=OWNER or actual['run_id']!=ctx.run_id:
        raise ValueError('resource UID/ownership mismatch; refuse mutation')
    return actual


def _inventory_record(ctx,resources,intents):
    readiness = getattr(ctx,'manifest',{}).get('readiness_check')
    record = {'project_id':ctx.config['gcp']['project_id'],'owner_id':OWNER,'run_id':ctx.run_id,
              'started_at':ctx.state['started_at'],'deadline':ctx.deadline,'resources':resources,
              'creation_intents':intents,'lease':ctx.state.get('lease')}
    if getattr(ctx,'manifest',{}).get('schedule')==COMPLETION_V2:
        record['infrastructure_seconds']=ctx.config['limits']['infrastructure_seconds']
    if readiness:
        from .readiness import cutoff
        record['deadline'] = cutoff(ctx)
        if handoff := ctx.state.get('readiness_handoff'):
            record['readiness_handoff'] = dict(handoff)
    return record


def _pin_inventory(ctx,record,*,cleanup=False):
    if not record['resources'] and not record.get('creation_intents'):raise ValueError('nonempty cleanup inventory required')
    path=ctx.work/'watchdog-inventory.json';write(path,record)
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    ctx.state['inventory']=upload_file(ctx,path,'control/'+ctx.run_id+'/inventory/'+digest+'.json',cleanup=cleanup)
    ctx.event('inventory-pinned',resource_count=len(record['resources']),creation_intent_count=len(record.get('creation_intents',[])))
    return record


def register_creation_intents(ctx,candidates):
    assert_project_lease(ctx)
    if ctx.state.get('creation_intents'):raise ValueError('prior creation is unresolved; inspect binding/status before retry')
    intents=[]
    # Bind later audit reconciliation to the identity actually making this request.
    creator=None
    try:
        impersonated=ctx.command(['gcloud','config','get-value','auth/impersonate_service_account'],check=False).stdout.strip()
        active=ctx.command(['gcloud','auth','list','--filter=status:ACTIVE','--format=value(account)'],check=False).stdout.strip()
        candidate_creator=impersonated if impersonated and impersonated!='(unset)' else active
        if re.fullmatch(r'[^\s@]+@[^\s@]+',candidate_creator):creator=candidate_creator
    except (OSError,RuntimeError,TimeoutError):pass
    for candidate in candidates:
        intent_id=uuid.uuid4().hex
        intents.append(dict(candidate,intent_id=intent_id,registered_at=time.time(),expected_creator=creator,binding_object='control/'+ctx.run_id+'/uid-bindings/'+intent_id+'.json'))
    ctx.state['creation_intents']=intents;ctx.state['pending_resources']=intents
    ctx.event('creation-intents-recorded')
    return _pin_inventory(ctx,_inventory_record(ctx,ctx.state.get('resources',[]),intents))


def resolve_creation_uid(ctx,intent,*,cleanup=False):
    """Read or CAS0-bind one first observation, shared by creation and cleanup."""
    assert_project_lease(ctx,cleanup=cleanup)
    bucket=ctx.state['terraform']['foundation']['evidence_bucket']
    expected_object='control/'+ctx.run_id+'/uid-bindings/'+intent['intent_id']+'.json'
    if intent.get('binding_object')!=expected_object or not re.fullmatch('[a-f0-9]{32}',intent['intent_id']):raise ValueError('unsafe creation UID binding')
    uri='gs://'+bucket+'/'+expected_object
    actual=cloud_get(ctx,intent,cleanup=cleanup)
    if actual is not None and intent.get('observed_uid') is not None and actual['uid']!=str(intent['observed_uid']):
        raise ValueError('disk UID changed since discovery; refuse binding')
    metadata=ctx.command(['gcloud','storage','objects','describe',uri,'--format=json'],check=False,cleanup=cleanup)
    if metadata.returncode:
        if not not_found(metadata):raise RuntimeError('UID binding cannot be observed')
        if actual is None:return None
        if actual['owner_id']!=OWNER or actual['run_id']!=ctx.run_id or not actual['uid']:raise ValueError('unowned creation intent cannot bind UID')
        value={'kind':intent['kind'],'name':intent['name'],'uid':actual['uid'],'project_id':ctx.config['gcp']['project_id'],'owner_id':OWNER,'run_id':ctx.run_id,'lease_token':ctx.state['lease']['token'],'intent_id':intent['intent_id']}
        path=ctx.work/'uid-bindings'/ (intent['intent_id']+'.json');write(path,value);os.chmod(path,0o600)
        created=ctx.command(['gcloud','storage','cp',str(path),uri,'--if-generation-match=0'],check=False,cleanup=cleanup)
        if created.returncode and not re.search(r'412|conditionNotMet|precondition',str(created.stderr),re.I):raise RuntimeError('UID binding persistence failed')
        metadata=ctx.command(['gcloud','storage','objects','describe',uri,'--format=json'],cleanup=cleanup)
    generation=str(json.loads(metadata.stdout).get('generation',''))
    if not generation.isdigit():raise ValueError('UID binding generation missing')
    value=doc(ctx,['gcloud','storage','cat',uri+'#'+generation],cleanup=cleanup)
    expected={'kind':intent['kind'],'name':intent['name'],'project_id':ctx.config['gcp']['project_id'],'owner_id':OWNER,'run_id':ctx.run_id,'lease_token':ctx.state['lease']['token'],'intent_id':intent['intent_id']}
    if any(value.get(key)!=item for key,item in expected.items()) or not value.get('uid'):raise ValueError('creation UID binding belongs to different target/lease')
    if actual is not None and (actual['uid']!=str(value['uid']) or actual['owner_id']!=OWNER or actual['run_id']!=ctx.run_id):raise ValueError('first-observed creation UID was replaced; refuse rebind')
    previous=[resource for resource in ctx.state.get('resources',[]) if resource['name']==intent['name']]
    if any(str(resource['uid'])!=str(value['uid']) for resource in previous):raise ValueError('creation intent conflicts with existing saved UID')
    return {'kind':intent['kind'],'name':intent['name'],'uid':str(value['uid']),'binding':{'uri':uri,'generation':generation}}


def _terminal_cluster_rejection(intent, audits, operations):
    """A rejected API request is different from an accepted operation failing later."""
    from datetime import datetime
    def stamp(value):
        return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
    def name(value):
        return str(value).split('/projects/',1)[-1].removeprefix('projects/').replace('/zones/','/locations/')
    if not intent.get('expected_creator') or intent.get('kind')!='cluster' or intent.get('observed_uid') or not isinstance(intent.get('registered_at'),(int,float)):
        return None
    target=name(intent['name']);started=intent['registered_at']
    for operation in operations:
        if name(operation.get('targetLink',''))!=target:continue
        if operation.get('operationType')!='CREATE_CLUSTER':continue
        try:before=stamp(operation['startTime'])<started
        except (KeyError,TypeError,ValueError):before=False
        if not before:return None
    matching=[]
    for audit in audits:
        payload=audit.get('protoPayload',{})
        try:recent=stamp(audit['timestamp'])>=started
        except (KeyError,TypeError,ValueError):continue
        if not recent or name(payload.get('resourceName',''))!=target:continue
        if payload.get('serviceName')!='container.googleapis.com' or not payload.get('methodName','').endswith('.CreateCluster'):continue
        # Even a named failed operation was accepted and may have created resources.
        if payload.get('response',{}).get('name') or audit.get('operation'):return None
        if payload.get('status',{}).get('code') not in (3,7,9):return None
        principal=payload.get('authenticationInfo',{}).get('principalEmail')
        if principal!=intent['expected_creator'] or not audit.get('insertId'):return None
        matching.append({'audit_insert_id':audit['insertId'],'request_at':audit['timestamp'],'principal':principal,
                         'status_code':payload['status']['code'],'intent_id':intent['intent_id'],'resource':intent['name']})
    return matching[0] if len(matching)==1 else None


def _reconcile_rejected_cluster(ctx,intent):
    """Optional read-only audit reconciliation; unavailable proof leaves intent pending."""
    from datetime import datetime,timezone
    if intent.get('kind')!='cluster' or 'registered_at' not in intent:return None
    after=datetime.fromtimestamp(intent['registered_at'],timezone.utc).isoformat()
    project=ctx.config['gcp']['project_id'];zone=ctx.config['gcp']['zone']
    query='protoPayload.serviceName="container.googleapis.com" AND protoPayload.methodName:"CreateCluster" AND timestamp>="'+after+'"'
    try:
        audits=doc(ctx,['gcloud','logging','read',query,'--project='+project,'--limit=100','--format=json'],cleanup=True)
        operations=doc(ctx,['gcloud','container','operations','list','--location='+zone,'--project='+project,'--format=json'],cleanup=True)
        if not isinstance(audits,list) or not isinstance(operations,list) or len(audits)>=100:return None
        receipt=_terminal_cluster_rejection(intent,audits,operations)
        if receipt is None or cloud_get(ctx,intent,cleanup=True) is not None:return None
        directory=ctx.result/'infrastructure/rejected-creations'/intent['intent_id']
        write(directory/'audit.json',audits);write(directory/'operations.json',operations)
        write(directory/'resolution.json',dict(receipt,absence_observed_at=time.time(),no_uid_binding=True))
        return receipt
    except (ValueError,RuntimeError,OSError,TimeoutError) as exc:
        ctx.event('creation-rejection-proof-unavailable',intent_id=intent['intent_id'],reason=str(exc))
        return None


def resolve_creation_intents(ctx,*,cleanup=False):
    """First observed UID is CAS0-persisted before it can enter deletion inventory."""
    assert_project_lease(ctx,cleanup=cleanup)
    pending=[];resolved=[]
    for intent in ctx.state.get('creation_intents',[]):
        bound=resolve_creation_uid(ctx,intent,cleanup=cleanup)
        if bound is None:
            rejection=_reconcile_rejected_cluster(ctx,intent) if cleanup else None
            if rejection is None:pending.append(intent)
            else:ctx.state.setdefault('rejected_creation_receipts',[]).append(rejection)
        else:resolved.append(bound)
    by_name={r['name']:r for r in ctx.state.get('resources',[])}
    by_name.update({r['name']:r for r in resolved});ctx.state['resources']=list(by_name.values())
    ctx.state['creation_intents']=pending;ctx.state['pending_resources']=pending
    ctx.event('creation-bindings-observed',unresolved_creation_intent_count=len(pending))
    return resolved,pending


def refresh_inventory(ctx,*,cleanup=False):
    require_context(ctx);assert_project_lease(ctx,cleanup=cleanup);
    if ctx.state.get('creation_intents'):resolve_creation_intents(ctx,cleanup=cleanup)
    g=ctx.config['gcp'];outputs=ctx.state.get('terraform',{});resources=[]
    controller=outputs.get('controller',{});cluster=outputs.get('cluster',{})
    names=[]
    if controller.get('controller_name'):names.append(('instance',f"projects/{g['project_id']}/zones/{g['zone']}/instances/{controller['controller_name']}"))
    if controller.get('router_resource_name'):names.append(('router',controller['router_resource_name']))
    for kind,key in [('network','network_resource_name'),('subnetwork','subnetwork_resource_name'),('firewall','firewall_resource_name')]:
        if controller.get(key):names.append((kind,controller[key]))
    if cluster.get('cluster_resource_name'):names.append(('cluster',cluster['cluster_resource_name']))
    disks=doc(ctx,['gcloud','compute','disks','list','--project='+g['project_id'],'--filter=labels.evaluation-owner='+OWNER+' AND labels.evaluation-run='+ctx.run_id,'--format=json'],cleanup=cleanup)
    for disk in disks:
        if disk.get('labels',{}).get('evaluation-owner')!=OWNER or disk.get('labels',{}).get('evaluation-run')!=ctx.run_id:
            raise ValueError('disk list returned unowned object')
        name=disk['selfLink'].split('/compute/v1/')[-1]
        names.append(('disk',name))
    previous={r['name']:r for r in ctx.state.get('resources',[])}
    for kind,name in names:
        candidate={'kind':kind,'name':name}
        actual=cloud_get(ctx,candidate,cleanup=cleanup)
        if actual is None:continue
        if actual['owner_id']!=OWNER or actual['run_id']!=ctx.run_id or not actual['uid']:
            raise ValueError('unowned resource cannot enter watchdog inventory')
        if name in previous and previous[name]['uid']!=actual['uid']:
            raise ValueError('UID changed without explicit inventory retirement')
        resource=candidate|{'uid':actual['uid']}
        if previous.get(name,{}).get('binding'):resource['binding']=previous[name]['binding']
        resources.append(resource)
    ctx.state['resources']=resources
    # Preserve independently bound intents even if Terraform never returned outputs.
    seen={r['name'] for r in resources}
    resources += [r for r in previous.values() if r['name'] not in seen and r.get('binding')]
    ctx.state['resources']=resources
    return _pin_inventory(ctx,_inventory_record(ctx,resources,ctx.state.get('creation_intents',[])),cleanup=cleanup)


def _watchdog_env(ctx,*,cleanup=False):
    outputs=ctx.state['terraform']['controller'];inventory=ctx.state['inventory'];g=ctx.config['gcp']
    # Cloud Run caps service name + tag at 46; reserve a meaningful digest.
    budget=46-len(outputs['watchdog_service_name'].rsplit('/',1)[-1])
    if budget<8:raise ValueError('watchdog service name leaves insufficient traffic tag space')
    tag='i'+hashlib.sha256((inventory['object']+'#'+inventory['generation']).encode()).hexdigest()[:min(16,budget-1)]
    value=doc(ctx,['gcloud','run','services','update',outputs['watchdog_service_name'],'--region='+g['region'],'--project='+g['project_id'],'--no-traffic','--tag='+tag,'--update-env-vars=INVENTORY_BUCKET='+ctx.state['terraform']['foundation']['evidence_bucket']+',INVENTORY_OBJECT='+inventory['object']+',INVENTORY_GENERATION='+inventory['generation'],'--format=json'],timeout=600,cleanup=cleanup)
    traffic=value.get('status',{}).get('traffic',value.get('trafficStatuses',[]))
    matches=[item for item in traffic if item.get('tag')==tag]
    if len(matches)!=1:raise ValueError('new watchdog tagged revision is not verifiable')
    candidate={'url':matches[0].get('url',matches[0].get('uri','')),'revision':matches[0].get('revisionName',matches[0].get('revision','')).split('/')[-1]}
    if not candidate['url'].startswith('https://') or not re.fullmatch('[a-z][a-z0-9-]{0,62}',candidate['revision']):raise ValueError('invalid watchdog candidate endpoint')
    return candidate


def watchdog_identity_token(ctx,service_account,audience,*,cleanup=False):
    """Generate only an ID token; keep existing OpenIdTokenCreator IAM scope."""
    from urllib.parse import quote
    access=ctx.command(['gcloud','auth','print-access-token'],cleanup=cleanup).stdout.strip()
    if not access:raise ValueError('caller access token missing')
    uri='https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/'+quote(service_account,safe='')+':generateIdToken'
    payload=json.dumps({'audience':audience,'includeEmail':True})
    response=ctx.command(['curl','--fail','--silent','--show-error','--request','POST','--header','Content-Type: application/json','--data-binary',payload,'--config','-',uri],input_text='header = "Authorization: Bearer '+access+'"\n',cleanup=cleanup)
    token=json.loads(response.stdout).get('token')
    if not isinstance(token,str) or not token.strip():raise ValueError('authenticated watchdog ID token missing')
    return token.strip()


def _same_watchdog_endpoint(actual,expected):
    """Only the optional single slash on the same HTTPS root is equivalent."""
    from urllib.parse import urlsplit
    def root(value):
        if not isinstance(value,str):return None
        try:parsed=urlsplit(value)
        except ValueError:return None
        if parsed.scheme!='https' or not parsed.netloc or parsed.path not in ('','/') or parsed.query or parsed.fragment or parsed.username is not None:return None
        return parsed.netloc
    reference=root(expected)
    return reference is not None and root(actual)==reference


def arm_watchdog(ctx,*,refresh=True,cleanup=False):
    require_context(ctx)
    assert_project_lease(ctx,cleanup=cleanup)
    if refresh:refresh_inventory(ctx,cleanup=cleanup)
    outputs=ctx.state['terraform']['controller'];g=ctx.config['gcp'];name=outputs['scheduler_name']
    # GCP keeps the old ready revision serving until replacement is ready.
    # Never pause delivery or change production into dry-run during refresh.
    candidate=_watchdog_env(ctx,cleanup=cleanup)
    uri=outputs['watchdog_uri'];scheduler_sa=ctx.state['terraform']['foundation']['scheduler_service_account']
    token=watchdog_identity_token(ctx,scheduler_sa,uri,cleanup=cleanup)
    if not token:raise ValueError('authenticated watchdog test token missing')
    # Token goes via stdin to curl configuration, never into argv/events/evidence.
    attempts=1 if ctx.state.get('ever_watchdog_armed') else 7
    for attempt in range(attempts):
        response=ctx.command(['curl','--fail','--silent','--show-error','--request','POST','--header','X-Karthos-Dry-Run: true','--config','-',candidate['url']],
                             input_text='header = "Authorization: Bearer '+token+'"\n',check=False,cleanup=cleanup)
        if response.returncode==0:break
        if attempt+1==attempts or not re.search(r'The requested URL returned error: 403\s*$',response.stderr or ''):
            raise RuntimeError('watchdog candidate dry-run request failed: '+(response.stderr or '')[-1200:])
        # The service-level invoker policy can take time to become effective
        # after first creation. Retry only this same authenticated dry-run.
        ctx.event('watchdog-authorization-retry',http_status=403,attempt=attempt+1)
        time.sleep(20)
    result=json.loads(response.stdout)
    if result.get('errors') or result.get('refused') or not result.get('not_hard_guarantee') or str(result.get('inventory_generation',''))!=ctx.state['inventory']['generation']:
        raise ValueError('watchdog dry-run failed')
    ctx.command(['gcloud','run','services','update-traffic',outputs['watchdog_service_name'],'--region='+g['region'],'--project='+g['project_id'],'--to-revisions='+candidate['revision']+'=100'],cleanup=cleanup)
    service=doc(ctx,['gcloud','run','services','describe',outputs['watchdog_service_name'],'--region='+g['region'],'--project='+g['project_id'],'--format=json'],cleanup=cleanup)
    traffic=service.get('status',{}).get('traffic',service.get('trafficStatuses',[]))
    if not any(item.get('revisionName',item.get('revision','')).split('/')[-1]==candidate['revision'] and item.get('percent')==100 for item in traffic):raise ValueError('new verified watchdog revision is not serving all traffic')
    actual=doc(ctx,['gcloud','scheduler','jobs','describe',name,'--project='+g['project_id'],'--format=json'],cleanup=cleanup)
    if actual.get('state')=='PAUSED':
        ctx.command(['gcloud','scheduler','jobs','resume',name,'--project='+g['project_id']],cleanup=cleanup)
        actual=doc(ctx,['gcloud','scheduler','jobs','describe',name,'--project='+g['project_id'],'--format=json'],cleanup=cleanup)
    if actual.get('state')!='ENABLED' or not _same_watchdog_endpoint(actual.get('httpTarget',{}).get('uri'),uri) or actual.get('httpTarget',{}).get('oidcToken',{}).get('serviceAccountEmail')!=scheduler_sa or actual.get('httpTarget',{}).get('oidcToken',{}).get('audience')!=uri:
        ctx.event('watchdog-readiness-failed');raise ValueError('watchdog Scheduler readiness not verified')
    ctx.state['watchdog_armed']=True;ctx.state['ever_watchdog_armed']=True;ctx.state['scheduler_name']=name
    if 'controller' in ctx.state.get('tfvars',{}):
        ctx.state['tfvars']['controller'].update(watchdog_enabled=True,inventory_object=ctx.state['inventory']['object'],inventory_generation=ctx.state['inventory']['generation'])
    ctx.event('watchdog-armed')
    return result


def commit_readiness_handoff(ctx, latest, *, clock=time.time):
    from .readiness import RESERVE, cutoff
    if not clock() < latest <= cutoff(ctx)-RESERVE:
        raise TimeoutError('readiness handoff no longer fits the deadline')
    if 'readiness_handoff' in ctx.state:
        raise ValueError('readiness handoff already attempted for this launch')
    ctx.state['readiness_handoff'] = {'study_deadline':ctx.deadline,'commit_before':latest}
    # Arm and verify an inventory that will keep the short deadline if the
    # controller exits before the create-only handoff object is committed.
    result = arm_watchdog(ctx)
    if result.get('effective_deadline') != cutoff(ctx):
        raise ValueError('watchdog candidate did not attest the readiness deadline')
    inventory = ctx.state['inventory']
    if clock() >= latest:
        raise TimeoutError('readiness watchdog verification exhausted handoff time')
    path = ctx.work/'readiness-handoff-commit.json'
    write(path, {'run_id':ctx.run_id,'lease_token':ctx.state['lease']['token'],
                 'inventory_object':inventory['object'],'inventory_generation':inventory['generation'],
                 'study_deadline':ctx.deadline})
    receipt = upload_file(ctx,path,'control/'+ctx.run_id+'/readiness-handoff.json')
    metadata = doc(ctx,['gcloud','storage','objects','describe',
                        receipt['uri']+'#'+receipt['generation'],'--raw','--format=json'])
    created = datetime.fromisoformat(metadata['timeCreated'].replace('Z','+00:00')).timestamp()
    if str(metadata.get('generation')) != receipt['generation'] or not ctx.state['started_at'] <= created < latest:
        raise TimeoutError('readiness commit generation or storage timestamp did not verify')
    ctx.state['readiness_handoff'].update(inventory_object=inventory['object'], inventory_generation=inventory['generation'])
    ctx.state['readiness_handoff_at'] = created
    ctx.event('readiness-handoff-committed',commit_generation=receipt['generation'])
    return {'verified':True,'uri':receipt['uri'],'generation':receipt['generation'],
            'inventory_generation':inventory['generation'],'committed_at':created}


def disarm_watchdog(ctx):
    require_context(ctx,cleanup=True)
    assert_project_lease(ctx,cleanup=True)
    if ctx.state.get('creation_intents'):raise ValueError('cannot pause deadline with unresolved creation intent')
    if any(verify_resource(ctx,resource,cleanup=True) is not None for resource in ctx.state.get('resources',[])):
        raise ValueError('cannot pause deadline before all owned resources are absent')
    name=ctx.state.get('scheduler_name') or ctx.state.get('terraform',{}).get('controller',{}).get('scheduler_name')
    if name:ctx.command(['gcloud','scheduler','jobs','pause',name,'--project='+ctx.config['gcp']['project_id']],cleanup=True)
    ctx.state['watchdog_armed']=False
    if 'controller' in ctx.state.get('tfvars',{}):ctx.state['tfvars']['controller']['watchdog_enabled']=False
    ctx.event('watchdog-disarmed')


def create_cluster(ctx,repeat,*,credentials=True):
    require_context(ctx)
    assert_project_lease(ctx)
    if repeat not in (1,2):raise ValueError('protocol has exactly two cluster generations')
    v4=ctx.manifest.get('schedule') in RECOVERABLE_SCHEDULES
    if repeat==2 and ctx.state.get('cluster_generation',0)>=2 and not v4:raise ValueError('second generation was already created; refuse extra rebuild')
    existing=[r for r in ctx.state.get('resources',[]) if r['kind']=='cluster']
    if existing and (repeat==1 or (v4 and ctx.state.get('cluster_generation')==repeat)):
        for resource in existing:
            if verify_resource(ctx,resource) is None:raise ValueError('precreated cluster unexpectedly absent')
    else:
        if existing:
            from .teardown import destroy_cluster
            removed=destroy_cluster(ctx)
            if removed.get('residual') or removed.get('errors') or removed.get('unresolved_creation_intents'):
                raise RuntimeError('cluster rebuild blocked: previous cleanup is unresolved')
        elif repeat==2 and not ctx.state.get('cluster_generation'):
            raise ValueError('second generation requires recorded first generation')
        variables=cluster_variables(ctx)
        expected_name='karthos-eval-'+hashlib.sha256(json.dumps([OWNER,ctx.run_id],separators=(',',':')).encode()).hexdigest()[:20]
        intended={'kind':'cluster','name':'projects/'+ctx.config['gcp']['project_id']+'/locations/'+ctx.config['gcp']['zone']+'/clusters/'+expected_name}
        register_creation_intents(ctx,[intended]);ctx.event('cluster-create-intent',repeat=repeat)
        arm_watchdog(ctx,refresh=False)
        tf_init(ctx,'cluster');tf_apply(ctx,'cluster',variables)
        ctx.state['cluster_generation']=repeat;ctx.event('cluster-created',repeat=repeat)
        refresh_inventory(ctx)
        if ctx.state.get('ever_watchdog_armed'):arm_watchdog(ctx)
    if credentials:
        name=ctx.state['terraform']['cluster']['cluster_name'];g=ctx.config['gcp']
        ctx.command(['env','KUBECONFIG='+str(ctx.kubeconfig),'gcloud','container','clusters','get-credentials',name,'--internal-ip','--zone='+g['zone'],'--project='+g['project_id']])
        from .auth_timing import install as instrument_auth
        instrument_auth(ctx.kubeconfig,ctx.result/'infrastructure/auth-timing.ndjson')
        ctx.event('cluster-credentials-resolved',repeat=repeat)
    return ctx.state['terraform']['cluster']


def publish_bootstrap_context(ctx):
    allowed=['run_id','owner_id','started_at','deadline','terraform','resources','inventory','scheduler_name','watchdog_armed','cluster_generation','manifest_object','ever_watchdog_armed','lease','cleanup_config','cleanup_config_sha256','cleanup_target','cleanup_target_sha256','project_number']
    value={key:ctx.state[key] for key in allowed if key in ctx.state}
    value.update(schema_version=1,project_id=ctx.config['gcp']['project_id'],phase='provisioned')
    path=ctx.work/'bootstrap-context.json';write(path,value)
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    ctx.state['bootstrap_context']=upload_file(ctx,path,'control/'+ctx.run_id+'/bootstrap/'+digest+'.json')
    ctx.event('bootstrap-context-pinned')
    return ctx.state['bootstrap_context']


def start_controller(ctx):
    require_context(ctx)
    if ctx.state.get('runtime_requested'):raise ValueError('runtime handoff already requested; inspect actual status before recovery')
    if ctx.state.get('phase')!='provisioned' or not ctx.state.get('watchdog_armed') or not ctx.state.get('bootstrap_context'):
        raise ValueError('provisioned context and armed watchdog required')
    assert_project_lease(ctx)
    boot=ctx.state['bootstrap_context'];controller=ctx.state['terraform']['controller']['controller_name'];g=ctx.config['gcp']
    expected=[resource for resource in ctx.state.get('resources',[]) if resource['kind']=='instance' and resource['name'].endswith('/'+controller)]
    if len(expected)!=1 or verify_resource(ctx,expected[0]) is None:raise ValueError('controller UID inventory missing or stale')
    # Persist before mutation. An interrupted handoff must never start a second controller.
    ctx.state['runtime_requested']=True;ctx.event('controller-handoff-intent')
    ctx.command(['gcloud','compute','instances','add-metadata',controller,'--zone='+g['zone'],'--project='+g['project_id'],'--metadata=runtime-enabled=true,bootstrap-context-uri='+boot['uri']+',bootstrap-context-generation='+boot['generation']])
    # Graceful shutdown flushes freshly extracted image layers; hard reset can lose writes.
    ctx.command(['gcloud','compute','instances','stop',controller,'--zone='+g['zone'],'--project='+g['project_id'],'--quiet'])
    ctx.command(['gcloud','compute','instances','start',controller,'--zone='+g['zone'],'--project='+g['project_id'],'--quiet'])
    ctx.event('controller-handoff-requested')
    return {'phase':'controller-handoff-requested','runtime_complete':False}


def request_final_cleanup(ctx,export_receipt):
    """Pin independently verifiable sealed-summary proof before early cleanup.

    Accepting a request is not proof of deletion. Watchdog persists observations
    under control/<run>/teardown/ and pauses itself only after complete absence.
    """
    require_context(ctx)
    if export_receipt.get('verified') is not True:
        raise ValueError('verified sealed final-summary export required')
    bucket=ctx.state['terraform']['foundation']['evidence_bucket']
    prefix='gs://'+bucket+'/gke-qualification-v1/runs/'+ctx.run_id+'/final-summary/'
    if export_receipt.get('uri')!=prefix:
        raise ValueError('final summary receipt targets a different run/bucket')
    def capture(name):
        uri=prefix+name
        metadata=doc(ctx,['gcloud','storage','objects','describe',uri,'--format=json'],cleanup=True)
        generation=str(metadata.get('generation',''))
        if not generation.isdigit():raise ValueError('summary object generation missing')
        raw=ctx.command(['gcloud','storage','cat',uri+'#'+generation],binary=True,cleanup=True).stdout
        if isinstance(raw,str):raw=raw.encode()
        return raw,{'bucket':bucket,'object':uri.split(bucket+'/',1)[1],'generation':generation,'sha256':hashlib.sha256(raw).hexdigest()}
    seal,seal_ref=capture('GKE-SHA256SUMS');refs=[seal_ref];declared={}
    for line in seal.decode().splitlines():
        checksum,name=line.split('  ',1)
        if not re.fullmatch('[a-f0-9]{64}',checksum) or Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError('unsafe final-summary checksum seal')
        declared[name]=checksum
    if 'run-report.json' not in declared:raise ValueError('sealed run-report.json missing')
    for name,checksum in declared.items():
        raw,ref=capture(name)
        if ref['sha256']!=checksum:raise ValueError('final-summary seal mismatch')
        refs.append(ref)
    mark_lease_cleanup(ctx)
    record=refresh_inventory(ctx,cleanup=True)
    required={'instance','router','firewall','subnetwork','network'}
    if not required <= {resource['kind'] for resource in record['resources']}:
        raise ValueError('complete compute/network UID inventory required')
    now=time.time()
    if now>ctx.deadline:raise TimeoutError('completion request is past deadline; deadline watchdog remains authoritative')
    record.update(completed_export_verified=True,cleanup_requested_at=now,export_receipt={'objects':refs,'uri':prefix})
    path=ctx.work/'completion-cleanup-inventory.json';write(path,record)
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    ctx.state['inventory']=upload_file(ctx,path,'control/'+ctx.run_id+'/completion/'+digest+'.json',cleanup=True)
    ctx.event('final-cleanup-request-intent')
    arm_watchdog(ctx,refresh=False,cleanup=True)
    ctx.state['final_cleanup_requested']=True
    ctx.event('final-cleanup-requested',completion_confirmed=False)
    return {'cleanup_requested':True,'completion_confirmed':False,'not_hard_guarantee':True,
            'result_prefix':'gs://'+bucket+'/control/'+ctx.run_id+'/teardown/',
            'inventory_generation':ctx.state['inventory']['generation'],
            'cloud_run_definition_retained':True,'cloud_run_min_instances':0,'active_instances_verified':False}
