"""One bounded, owned checkout replacement after the first cluster's last baseline."""
from datetime import datetime,timezone
from types import SimpleNamespace
import time

from . import export
from .consumer import capture_consumer
from .diagnostics.restarts import observe_restart,restart_target
from .models import THESIS_HISTORY_SECONDS,read
from .thesis import replica_inventory,score_history
from ..consumers.api_only import APIConsumer
from ..protocol import source_id
from .. import runner as r

EXPORT_RESERVE_SECONDS=120
CONSUMER_RESERVE_SECONDS=175


def capture(ctx,cell,spec):
    started=time.time()
    deadline=min(started+THESIS_HISTORY_SECONDS,ctx.deadline-ctx.config['limits']['cleanup_reserve_seconds'])
    operation_end=deadline-EXPORT_RESERVE_SECONDS
    monotonic_start=time.monotonic()
    monotonic_end=monotonic_start+(operation_end-started)
    previous={name:getattr(ctx,name,None) for name in ('phase_deadline','phase_reserve')}
    runner_deadline=r._DEADLINE
    ctx.phase_deadline=operation_end
    ctx.phase_reserve=0
    r._DEADLINE=min(runner_deadline,monotonic_end-8) if runner_deadline is not None else monotonic_end-8
    path=ctx.result/'thesis-history'
    try:
        path.mkdir(exist_ok=False)
        report={'answer':{'state':'insufficient','reason':'replacement evidence unavailable'},
                'continuation_safe':False,'duration_bound_seconds':THESIS_HISTORY_SECONDS,
                'started_at':started,'deadline':deadline,'operation_deadline':operation_end}
        try:
            _capture(ctx,cell,spec,path,monotonic_end,report)
        except Exception as exc:
            report['answer']={'state':'insufficient','reason':str(exc) or type(exc).__name__}
        report['observation_and_cleanup_seconds']=time.monotonic()-monotonic_start
        ctx.write(path/'report.json',report)
        ctx.phase_deadline=deadline
        try:
            if time.monotonic()-monotonic_start>=deadline-started:
                raise TimeoutError('history export deadline reached before publication')
            report['export']=export.upload(ctx,path,bucket=ctx.state['terraform']['foundation']['evidence_bucket'],
                prefix='gke-qualification-v1/runs/'+ctx.run_id+'/thesis-history')
        except Exception as exc:
            report['export']={'verified':False,'reason':str(exc) or type(exc).__name__}
        report['elapsed_seconds']=time.monotonic()-monotonic_start
        if report['elapsed_seconds']>=deadline-started or report['export'].get('verified') is not True:
            report['continuation_safe']=False
            report['handoff_reason']='history publication did not verify within the bounded phase'
        return report
    finally:
        r._DEADLINE=runner_deadline
        for name,value in previous.items():
            if value is None:
                delattr(ctx,name)
            else:
                setattr(ctx,name,value)


def _capture(ctx,cell,spec,path,deadline,report):
    service='checkoutservice'
    namespace=cell.namespace
    def remaining(reserve=0):
        seconds=deadline-time.monotonic()-reserve
        if seconds<=0:
            raise TimeoutError('checkout history observation deadline reached')
        return seconds

    def graph_contains(client,stamp,expected,excluded=()):
        graph=client.graph(at=stamp)
        if graph.get('state')!='observed':
            return None
        nodes={n.get('name') for n in graph['nodes'] if n.get('kind')=='pod' and n.get('namespace')==namespace}
        return graph if expected<=nodes and not set(excluded)&nodes else None

    deployment=ctx.json('get','deployment',service,'-n',namespace)
    selector=','.join(k+'='+v for k,v in sorted(deployment['spec']['selector']['matchLabels'].items()))
    original=ctx.json('get','pods','-n',namespace,'-l',selector)
    before=replica_inventory(ctx,namespace,service)
    ctx.write(path/'kubernetes-before.json',before)
    original_uids={p['metadata']['uid'] for p in original['items'] if not p['metadata'].get('deletionTimestamp')}
    if not before or original_uids!={p['uid'] for p in before}:
        raise ValueError('independent checkout inventory differs from the pre-replacement deployment Pod set')
    with r.forward('deployment/karthos-backend','karthos-system',28983,8080) as base:
        old_names={p['pod'] for p in before}
        client=APIConsumer(base,timeout=5,max_pages=20,timeout_cap=lambda:remaining(8))
        before_at=datetime.now(timezone.utc).isoformat()
        earlier=graph_contains(client,before_at,old_names)
        if not earlier:
            raise ValueError('pre-replacement retained snapshot does not contain the observed checkout replicas')
        ctx.write(path/'snapshot-before.json',earlier)

        def restart_kube(*args,**kwargs):
            kwargs['timeout']=min(kwargs.get('timeout',30),remaining(CONSUMER_RESERVE_SECONDS))
            return ctx.kube(*args,**kwargs)
        remaining(CONSUMER_RESERVE_SECONDS+30)
        local=SimpleNamespace(kube=restart_kube,write=ctx.write,evidence_dir=path)
        report['restart']=restart_target(local,namespace,service,ctx.run_id)
        _,replacements=observe_restart(restart_kube,namespace,service,selector,ctx.run_id,deployment,original,
                                      timeout=min(180,remaining(CONSUMER_RESERVE_SECONDS)))
        after=replica_inventory(ctx,namespace,service)
        ctx.write(path/'kubernetes-after.json',after)
        new_uids={p['metadata']['uid'] for p in replacements['items'] if not p['metadata'].get('deletionTimestamp')}
        if (not after or {row['uid'] for row in after}!=new_uids
                or any(not row.get('node') or row.get('health')!='healthy' for row in after)):
            raise ValueError('healthy checkout inventory changed after the observed replacement')
        report['replacement_verified']=True
        client.timeout_cap=lambda:remaining(CONSUMER_RESERVE_SECONDS)
        latest=None
        while time.monotonic()<deadline-CONSUMER_RESERVE_SECONDS:
            after_at=datetime.now(timezone.utc).isoformat()
            candidate=graph_contains(client,after_at,{p['pod'] for p in after},old_names)
            if candidate and candidate['snapshot']['id']!=earlier['snapshot']['id']:
                latest=candidate
                break
            time.sleep(max(0,min(5,deadline-CONSUMER_RESERVE_SECONDS-time.monotonic())))
        if latest:
            ctx.write(path/'snapshot-after.json',latest)

    def verify_stable_replacement():
        if replica_inventory(ctx,namespace,service)!=after:
            raise ValueError('healthy checkout inventory changed during the history observation')
        remaining()

    if latest is None:
        verify_stable_replacement()
        report['answer']={'state':'insufficient','reason':'newer retained checkout snapshot unavailable within the observation budget'}
        report['consumer']={'state':'unattempted','cleanup_verified':True}
        report['continuation_safe']=True
        return

    ctx.write(path/'manifest.json',{'start':before_at,'end':after_at,'namespace':namespace,'source_id':source_id(cell.source)})
    status=capture_consumer(ctx,path,'history',spec=spec,timeout=min(110,remaining(10)),deadline=deadline-10)
    report['consumer']=status
    if status.get('cleanup_verified') is not True:
        raise ValueError('owned API-only consumer cleanup did not verify')
    verify_stable_replacement()
    report['continuation_safe']=True
    if status.get('valid') is not True or status.get('job_completed') is not True:
        raise ValueError('API-only consumer Job completion or contract did not verify')
    doc=read(path/'consumer/ledger.json')
    if doc['before']['snapshot']!=earlier['snapshot'] or doc['after']['snapshot']!=latest['snapshot']:
        raise ValueError('API-only consumer selected different retained snapshots from the independently observed replacement')
    report['answer']=score_history(doc,before,after)
