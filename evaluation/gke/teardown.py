"""UID/ownership-gated cleanup. Protected foundation is never destroyed."""
from __future__ import annotations
import contextlib,fcntl,hashlib,json,os,subprocess,time
from pathlib import Path
from .models import read,write,identifier,canonical_sha
from .provision import COMPUTE_CLI_PATHS,require_context,verify_resource,tf_apply,disarm_watchdog,arm_watchdog,cloud_get,tf_init,cluster_variables,_controller_vars,assert_project_lease,mark_lease_cleanup,release_project_lease,resolve_creation_intents,resolve_creation_uid


def destroy_cluster(ctx):
    require_context(ctx,cleanup=True)
    if any(intent['kind']=='cluster' for intent in ctx.state.get('creation_intents',[])):
        _,unresolved=resolve_creation_intents(ctx,cleanup=True)
        waiting=[intent['name'] for intent in unresolved if intent['kind']=='cluster']
        if waiting:return {'residual':waiting,'unresolved_creation_intents':waiting,'not_hard_guarantee':True}
    resources=[r for r in ctx.state.get('resources',[]) if r['kind']=='cluster']
    if not resources:
        if ctx.state.get('terraform',{}).get('cluster'):
            raise ValueError('cluster Terraform state exists without verified UID inventory')
        return {'residual':[]}
    for resource in resources:verify_resource(ctx,resource,cleanup=True)
    was_armed=ctx.state.get('watchdog_armed',False)
    try:
        variables=ctx.state.get('tfvars',{}).get('cluster') or cluster_variables(ctx)
        if variables is None:raise ValueError('reviewed cluster tfvars missing; rebuild from remote state first')
        if ctx.state.get('terraform',{}).get('cluster'):
            tf_init(ctx,'cluster',cleanup=True)
            tf_apply(ctx,'cluster',variables,destroy=True,cleanup=True)
        else:
            for resource in resources:delete_bound_cluster(ctx,resource)
        residual=[r['name'] for r in resources if verify_resource(ctx,r,cleanup=True) is not None]
        if residual:raise RuntimeError('cluster deletion not confirmed absent')
        ctx.state['resources']=[r for r in ctx.state['resources'] if r['kind']!='cluster']
        ctx.state['terraform'].pop('cluster',None)
        ctx.event('cluster-destroyed')
        # VM/router deadline cleanup remains independently armed across the
        # potentially long next-cluster creation; its retired UID is omitted.
        if was_armed:arm_watchdog(ctx,cleanup=True)
        return {'residual':[]}
    except Exception:
        # The prior armed revision/timer was never paused during deletion.
        if was_armed:
            try:arm_watchdog(ctx,cleanup=True)
            except Exception:ctx.event('cluster-destroy-failed-watchdog-refresh-unverified')
        raise


def delete_bound_cluster(ctx,resource):
    actual=verify_resource(ctx,resource,cleanup=True)
    if actual is None:return
    parts=resource['name'].split('/')
    ctx.command(['gcloud','container','clusters','delete',parts[-1],'--zone='+parts[3],'--project='+ctx.config['gcp']['project_id'],'--quiet'],timeout=1800,cleanup=True)
    ctx.state['terraform_cluster_state_reconciliation_pending']=True


def refresh_cleanup_disks(ctx):
    """Discover only this leased run's disks and persist UIDs before deletion.

    This augments operator cleanup's private inventory; it does not change the
    independent watchdog's frozen inventory or broaden deletion authority.
    """
    require_context(ctx,cleanup=True)
    assert_project_lease(ctx,cleanup=True)
    g=ctx.config['gcp']
    args=['gcloud','compute','disks','list','--project='+g['project_id'],
          '--filter=labels.evaluation-owner=gke-qualification-v1 AND labels.evaluation-run='+ctx.run_id,
          '--format=json']
    rows=json.loads(ctx.command(args,cleanup=True).stdout)
    if not isinstance(rows,list):raise ValueError('owned disk list envelope invalid')
    previous={r['name']:r for r in ctx.state.get('resources',[])}
    added=[]
    for disk in rows:
        labels=disk.get('labels',{})
        if labels.get('evaluation-owner')!='gke-qualification-v1' or labels.get('evaluation-run')!=ctx.run_id:
            raise ValueError('owned disk list returned different ownership')
        link=disk.get('selfLink','')
        prefix='https://www.googleapis.com/compute/v1/projects/'+g['project_id']+'/'
        if not link.startswith(prefix):raise ValueError('owned disk list returned cross-project disk')
        name=link.split('/compute/v1/',1)[1]
        parts=name.split('/')
        if len(parts)!=6 or (parts[2],parts[3]) not in {('zones',g['zone']),('regions',g['region'])} or parts[4]!='disks':
            raise ValueError('owned disk list returned out-of-scope location')
        uid=str(disk.get('id',''))
        if not uid:raise ValueError('owned disk UID missing')
        if name in previous and str(previous[name]['uid'])!=uid:raise ValueError('disk UID changed; refuse rebind')
        resource={'kind':'disk','name':name,'uid':uid}
        actual=verify_resource(ctx,resource,cleanup=True)
        if actual is None:continue
        # Consult the same immutable first-UID object as the independent
        # watchdog, even when only that path previously observed this name.
        intent_id=hashlib.sha256(json.dumps([g['project_id'],'gke-qualification-v1',ctx.run_id,name],
                                          separators=(',',':')).encode()).hexdigest()[:32]
        intent={'kind':'disk','name':name,'intent_id':intent_id,'observed_uid':uid,
                'binding_object':'control/'+ctx.run_id+'/uid-bindings/'+intent_id+'.json'}
        intents={item['name']:item for item in ctx.state.get('cleanup_disk_intents',[])}
        if name in intents and intents[name].get('observed_uid')!=uid:
            raise ValueError('cleanup disk first observed UID replaced')
        intents[name]=intent;ctx.state['cleanup_disk_intents']=list(intents.values())
        ctx.event('cleanup-disk-binding-intent-recorded')
        bound=resolve_creation_uid(ctx,intent,cleanup=True)
        if bound is None:continue
        if str(bound['uid'])!=uid:raise ValueError('cleanup disk immutable UID differs')
        if name not in previous:added.append(name)
        previous[name]=bound
    ctx.state['resources']=list(previous.values())
    # event persists the exact resource UIDs in execution.json before caller
    # issues the first destructive operation. An event/write failure aborts.
    ctx.event('cleanup-disks-observed',new_disk_count=len(added))
    return ctx.state['resources']


def teardown(ctx):
    require_context(ctx,cleanup=True)
    resources=list(ctx.state.get('resources',[]));errors=[]
    if resources:assert_project_lease(ctx,cleanup=True)
    if ctx.state.get('creation_intents'):
        _,unresolved=resolve_creation_intents(ctx,cleanup=True)
        resources=list(ctx.state.get('resources',[]))
        if not unresolved and ctx.state.get('rejected_creation_receipts') and resources:
            from .provision import arm_watchdog
            arm_watchdog(ctx,refresh=False)
        if unresolved:
            names=[intent['name'] for intent in unresolved]
            ctx.event('teardown-partial-creation-unobserved',residual=names)
            return {'residual':names,'unresolved_creation_intents':names,'errors':[],'foundation_preserved':True,'not_hard_guarantee':True}
    elif ctx.state.get('pending_resources'):
        names=[resource['name'] for resource in ctx.state['pending_resources']]
        return {'residual':names,'errors':[{'stage':'legacy-unpinned-creation','type':'RecoveryRequired'}],'foundation_preserved':True,'not_hard_guarantee':True}
    if resources:
        resources=list(refresh_cleanup_disks(ctx))
    # Verify all identities before the first destructive operation. Ownership or
    # UID errors refuse everything, including disarming the independent watchdog.
    for resource in resources:verify_resource(ctx,resource,cleanup=True)
    if resources:mark_lease_cleanup(ctx)
    if not resources:
        if ctx.state.get('terraform',{}).get('controller') or ctx.state.get('terraform',{}).get('cluster'):
            raise ValueError('nonempty remote resources lack UID inventory; cleanup refused')
        if ctx.state.get('lease') and not ctx.state.get('project_lease_released'):
            residual=['project-lease-awaiting-inspected-empty-inventory-recovery']
            ctx.event('teardown-partial',residual=residual)
            return {'residual':residual,'errors':[],'foundation_preserved':True,'project_lease_released':False}
        ctx.event('teardown-complete',residual=[])
        return {'residual':[],'errors':[],'foundation_preserved':True}
    # Keep watchdog armed throughout final deletion; direct cleanup does not
    # recreate a resource, and a second UID observation precedes each operation.
    cluster=[r for r in resources if r['kind']=='cluster']
    if cluster:
        try:
            for resource in cluster:verify_resource(ctx,resource,cleanup=True)
            variables=ctx.state.get('tfvars',{}).get('cluster') or cluster_variables(ctx)
            if variables is None:raise ValueError('cluster tfvars unavailable')
            if ctx.state.get('terraform',{}).get('cluster'):
                tf_init(ctx,'cluster',cleanup=True)
                tf_apply(ctx,'cluster',variables,destroy=True,cleanup=True)
            else:
                for resource in cluster:delete_bound_cluster(ctx,resource)
        except Exception as exc:errors.append({'stage':'cluster','type':type(exc).__name__})
    # Cluster removal can leave or finish provisioning additional PVC disks.
    # A failed second observation must retain watchdog/lease, never claim empty.
    resources=list(refresh_cleanup_disks(ctx))
    for resource in resources:
        if resource['kind']!='disk':continue
        try:
            actual=verify_resource(ctx,resource,cleanup=True)
            if actual is None:continue
            if actual.get('value',{}).get('users'):
                raise ValueError('owned disk remains attached; retain cleanup controls')
            parts=resource['name'].split('/')
            ctx.command(['gcloud','compute','disks','delete',parts[-1],('--region=' if parts[2]=='regions' else '--zone=')+parts[3],'--project='+ctx.config['gcp']['project_id'],'--quiet'],cleanup=True)
        except Exception as exc:errors.append({'stage':'disk','name':resource['name'],'type':type(exc).__name__})
    # Do not delete watchdog/control-plane until cluster and retained disks are
    # observed absent. Failed/attached disks leave watchdog available for retries.
    blockers=[]
    for resource in resources:
        if resource['kind'] not in {'cluster','disk'}:continue
        try:
            if verify_resource(ctx,resource,cleanup=True) is not None:blockers.append(resource['name'])
        except Exception as exc:
            blockers.append(resource['name']);errors.append({'stage':'verify','name':resource['name'],'type':type(exc).__name__})
    controller=ctx.state.get('terraform',{}).get('controller')
    if controller and not blockers:
        try:
            for resource in resources:
                if resource['kind'] in {'instance','router','firewall','subnetwork','network'}:verify_resource(ctx,resource,cleanup=True)
            variables=ctx.state.get('tfvars',{}).get('controller') or _controller_vars(ctx)
            if variables is None:raise ValueError('controller tfvars unavailable')
            # Target only billable compute/network first; watchdog service and
            # Scheduler remain available if NAT/router/VM deletion is partial.
            tf_init(ctx,'controller',cleanup=True)
            tf_apply(ctx,'controller',variables,destroy=True,cleanup=True,targets=['google_compute_instance.controller','google_compute_router_nat.evaluation','google_compute_router.evaluation','google_compute_firewall.internal','google_compute_subnetwork.evaluation','google_compute_network.evaluation'])
        except Exception as exc:errors.append({'stage':'controller-billable','type':type(exc).__name__})
    residual=[]
    for resource in resources:
        try:
            if verify_resource(ctx,resource,cleanup=True) is not None:residual.append(resource['name'])
        except Exception as exc:
            residual.append(resource['name']);errors.append({'stage':'verify','name':resource['name'],'type':type(exc).__name__})
    if not residual and controller:
        try:
            # Only now can the cold independent service/timer be removed.
            disarm_watchdog(ctx)
            tf_apply(ctx,'controller',ctx.state.get('tfvars',{}).get('controller') or _controller_vars(ctx),destroy=True,cleanup=True)
            ctx.state['terraform'].pop('controller',None);ctx.state['terraform'].pop('cluster',None)
            ctx.state['resources']=[]
            release_project_lease(ctx,confirmed_absent=True)
        except Exception as exc:
            residual.append('controller-root-control-resources');errors.append({'stage':'controller-final','type':type(exc).__name__})
    result={'residual':residual,'errors':errors,'foundation_preserved':True,'not_hard_guarantee':True,'project_lease_released':bool(ctx.state.get('project_lease_released',False))}
    ctx.event('teardown-complete' if not residual else 'teardown-partial',residual=residual,error_count=len(errors))
    return result


class CleanupContext:
    """Existing exact private target, no authorization/readiness manufacture."""
    cleanup_only=True

    def __init__(self,root,run_id,*,executor=subprocess.run):
        self.root=Path(root).resolve();self.run_id=identifier(run_id);self.executor=executor
        data_root=Path(os.environ.get('GKE_EVAL_DATA_ROOT',self.root)).resolve()
        self.work=data_root/'.work/gke'/self.run_id
        state=self.work/'execution.json'
        if not state.is_file() or state.is_symlink():raise ValueError('existing private run inventory required for cleanup')
        self.state=read(state);self.config=self.state.get('cleanup_config',{})
        target=self.state.get('cleanup_target',{})
        if not self.config or canonical_sha(self.config)!=self.state.get('cleanup_config_sha256') or canonical_sha(target)!=self.state.get('cleanup_target_sha256'):
            raise ValueError('saved cleanup target fingerprint mismatch')
        expected={key:self.config['gcp'][key] for key in ['project_id','region','zone']}
        expected.update(owner_id='gke-qualification-v1',run_id=self.run_id)
        if target!=expected or self.state.get('run_id')!=self.run_id or self.state.get('owner_id')!='gke-qualification-v1':
            raise ValueError('saved target/run ownership mismatch')
        if not isinstance(self.state.get('resources'),list):raise ValueError('saved exact resource inventory missing')
        self.result=data_root/'results/gke-qualification-v1/runs'/self.run_id;self.result.mkdir(parents=True,exist_ok=True)
        self.deadline=self.state.get('deadline',0);self.kubeconfig=self.work/'kubeconfig';self.manifest={}

    def command(self,args,*,cleanup=False,timeout=600,input_text=None,check=True,binary=False,cwd=None,**kwargs):
        if not cleanup:raise ValueError('saved inventory context permits cleanup commands only')
        args=list(map(str,args))
        if args[0]=='terraform':
            if len(args)<3 or args[2] not in {'init','plan','apply'} or (args[2]=='plan' and '-destroy' not in args):
                raise ValueError('noncleanup Terraform command refused')
            if '/foundation' in args[1]:raise ValueError('foundation mutation refused')
        elif args[:2]==['gcloud','compute']:
            path=('networks','subnets') if args[2:4]==['networks','subnets'] else tuple(args[2:3])
            operation=2+len(path)
            scoped_list=False
            if args[:4]==['gcloud','compute','disks','list']:
                allowed_list=['gcloud','compute','disks','list','--project='+self.config['gcp']['project_id'],
                              '--filter=labels.evaluation-owner=gke-qualification-v1 AND labels.evaluation-run='+self.run_id,
                              '--format=json']
                scoped_list=args==allowed_list
            if not scoped_list and (path not in set(COMPUTE_CLI_PATHS.values()) or len(args)<=operation+1 or args[operation] not in {'describe','delete'}):raise ValueError('noncleanup compute command refused')
        elif args[:4] in (['gcloud','container','clusters','describe'],['gcloud','container','clusters','delete']):pass
        elif args[:3]==['gcloud','storage','cp'] and len(args)==6 and (
                (args[4]==self.state.get('lease',{}).get('uri') and args[5].startswith('--if-generation-match='))
                or (args[4] in {'gs://'+self.state['terraform']['foundation']['evidence_bucket']+'/'+intent['binding_object']
                               for intent in self.state.get('creation_intents',[])+self.state.get('cleanup_disk_intents',[])}
                    and args[5]=='--if-generation-match=0')):pass
        elif args[:3] in (['gcloud','storage','cat'],['gcloud','storage','rm']) or args[:4]==['gcloud','storage','objects','describe']:pass
        elif args[:4]==['gcloud','scheduler','jobs','pause']:pass
        elif args[:4]==['gcloud','run','services','update'] and any('DRY_RUN=true' in arg for arg in args):pass
        else:raise ValueError('unsupported emergency cleanup command')
        value=self.executor(args,cwd=cwd,input=input_text,text=not binary,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
        if check and value.returncode:raise RuntimeError('cleanup command failed; inspect exact residual resource status')
        return value

    def event(self,phase,**fields):
        self.state.update(phase=phase,**fields);write(self.work/'execution.json',self.state)
        with (self.result/'progress.ndjson').open('a') as out:out.write(json.dumps({'at':time.time(),'phase':phase,**fields})+'\n')

    @contextlib.contextmanager
    def controller_lock(self):
        with (self.work/'controller.lock').open('a+') as handle:
            try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RuntimeError('another controller holds the run lock')
            try:yield
            finally:fcntl.flock(handle,fcntl.LOCK_UN)


def load_cleanup_context(root,run_id,*,executor=subprocess.run):
    return CleanupContext(root,run_id,executor=executor)
