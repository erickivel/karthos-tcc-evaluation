"""One owned deployment restart request, with UID/resourceVersion preconditions."""
import json
from pathlib import Path
import time
from .resources import OWNER, OWNER_LABEL, RUN_LABEL
from ..models import identifier
from ...capture_watchdog import GATEWAY_RESTART, OWNER as CAPTURE_OWNER

RESTART_RUN='evaluation.karthos.dev/restart-run'
RESTART_AT='evaluation.karthos.dev/restart-at'


def restart_target(ctx, namespace, target, run_id, *, capture_plan=None):
    """ctx.kube returns CompletedProcess; ctx.write(path, record) persists evidence.

    target={kind:'deployment',name:'actual-name'}; string aliases checkout and
    gateway map to checkoutservice and karthos-gateway. This requests one rolling
    restart; completion must be observed independently by the enclosing window.
    ctx.clock defaults to epoch time; ctx.evidence_dir defaults to diagnostics.
    """
    identifier(namespace);identifier(run_id)
    if isinstance(target,str):
        target={'kind':'deployment','name':{'checkout':'checkoutservice','gateway':'karthos-gateway'}.get(target,target)}
    kind=target.get('kind','').lower();name=target.get('name','')
    if kind not in ('deployment','deployments'): raise ValueError('restart target must be a deployment')
    identifier(name)
    ledger=getattr(ctx,'_gke_restart_requests',None)
    if ledger is None: ledger=set();setattr(ctx,'_gke_restart_requests',ledger)
    key=(namespace,name,run_id)
    if key in ledger: raise ValueError('one scheduled restart per target/run has already been attempted')
    result=ctx.kube('get','deployment',name,'-n',namespace,'-o','json',check=False)
    if result.returncode: raise ValueError('restart target could not be read')
    obj=json.loads(result.stdout);meta=obj.get('metadata') or {}
    if meta.get('labels',{}).get(OWNER_LABEL)!=OWNER or meta.get('labels',{}).get(RUN_LABEL)!=run_id:
        raise ValueError('restart target is not explicitly owned by this evaluation run')
    if not meta.get('uid') or not meta.get('resourceVersion'):
        raise ValueError('restart target identity/version unavailable')
    template=(obj.get('spec') or {}).get('template') or {}
    annotations=(template.get('metadata') or {}).get('annotations') or {}
    if annotations.get(RESTART_RUN)==run_id: raise ValueError('restart already recorded on deployment template')
    clock=getattr(ctx,'clock',time.time);timestamp=clock()
    if capture_plan is None:
        updated=dict(annotations,**{RESTART_RUN:run_id,RESTART_AT:str(timestamp)})
    else:
        admitted=capture_plan.get('after_workload',{})
        token=capture_plan.get('gateway_restart_token')
        if (name != 'karthos-gateway' or capture_plan.get('kind') != 'gateway' or not token
                or not capture_plan.get('owner') or annotations.get(CAPTURE_OWNER) != capture_plan['owner']
                or admitted.get('metadata',{}).get('uid') != meta['uid']
                or admitted.get('spec',{}).get('template') != template or GATEWAY_RESTART in annotations):
            raise ValueError('Gateway restart differs from the guarded capture plan')
        updated=dict(annotations,**{GATEWAY_RESTART:token})
    patch=[{'op':'test','path':'/metadata/uid','value':meta['uid']},
           {'op':'test','path':'/metadata/resourceVersion','value':meta['resourceVersion']},
           {'op':'add','path':'/spec/template/metadata/annotations','value':updated}]
    record={'state':'scheduled','namespace':namespace,'kind':'deployment','name':name,
            'uid':meta['uid'],'resource_version':meta['resourceVersion'],'run_id':run_id,'requested_at':timestamp,
            'restart_count_requested':1,'completion':'not observed by this request function'}
    path=Path(getattr(ctx,'evidence_dir','diagnostics'))/('restart-'+run_id+'-'+name+'.json')
    # Persist intent before attempting one mutation; never retry a possibly applied request.
    ledger.add(key);ctx.write(path,record)
    try:
        response=ctx.kube('patch','deployment',name,'-n',namespace,'--type=json','-p',json.dumps(patch),check=False)
        record=dict(record,state='requested' if response.returncode==0 else 'failed',
                    completed_at=clock(),returncode=response.returncode,
                    stderr=response.stderr.decode() if isinstance(response.stderr,bytes) else response.stderr)
        ctx.write(path,record)
        if response.returncode: raise RuntimeError('owned restart patch failed; no automatic retry')
    except Exception as exc:
        ctx.write(path,dict(record,state='failed',error=str(exc)))
        raise
    return record


def observe_restart(kube, namespace, name, selector, run_id, before, pods_before, *, timeout):
    """Wait for a newer observed generation and a distinct, Ready Pod set."""
    from ...structured_capture import pod_ready

    original = before['metadata']
    previous_uids = {pod['metadata']['uid'] for pod in pods_before['items']
                     if not pod['metadata'].get('deletionTimestamp')}
    replicas = before['spec'].get('replicas', 1)
    if not original.get('uid') or not previous_uids or replicas < 1:
        raise ValueError('restart lacks a live pre-restart deployment and Pod identity')
    deadline = time.monotonic() + timeout

    def read(*args):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('restart replacement generation and Ready Pod UIDs not observed within budget')
        return json.loads(kube('get', *args, '-n', namespace, '-o', 'json',
                               timeout=min(10., remaining)).stdout)

    while True:
        deployment = read('deployment', name)
        metadata = deployment['metadata']
        labels = metadata.get('labels', {})
        if metadata.get('uid') != original['uid'] or labels.get(OWNER_LABEL) != OWNER or labels.get(RUN_LABEL) != run_id:
            raise ValueError('restart deployment identity or ownership changed')
        pods = read('pods', '-l', selector)
        active = [pod for pod in pods['items'] if not pod['metadata'].get('deletionTimestamp')]
        for pod in active:
            pod_labels = pod['metadata'].get('labels', {})
            if pod_labels.get(OWNER_LABEL) != OWNER or pod_labels.get(RUN_LABEL) != run_id:
                raise ValueError('restart replacement Pod ownership changed')
        current_uids = {pod['metadata'].get('uid') for pod in active}
        generation = metadata['generation']
        if (generation > original['generation']
                and deployment.get('status', {}).get('observedGeneration', 0) >= generation
                and len(active) == len(current_uids) == replicas
                and None not in current_uids and not (current_uids & previous_uids)
                and all(pod_ready(pod) for pod in active)):
            return deployment, pods
        time.sleep(min(1., max(0., deadline - time.monotonic())))
