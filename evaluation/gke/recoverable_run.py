"""V4 serial attempts: local evidence validity and independently verified transitions."""
from __future__ import annotations
import copy
import json
import math
import shutil
import time
import traceback
from pathlib import Path
from .cloud_identity import is_authorization_failure
from .models import canonical_sha, read, sha, write, COMPLETION, COMPLETION_V2, COMPLETION_SCHEDULES, REPAIR, REPAIR_V2, REPAIR_SCHEDULES, RECOVERABLE_SCHEDULES


class SafetyFailure(RuntimeError):
    """Ownership, uncontrolled execution or evidence preservation prevents continuation."""


class ProductDefect(SafetyFailure):
    """A demonstrated product-contract defect requires human direction."""


def history_required(schedule,matrix):
    return schedule not in REPAIR_SCHEDULES and any(row.get('history_observation',True) and row.get('repeat')==1 and row.get('cell')=='boutique-mesh' and row.get('phase')=='baseline' for row in matrix)


def scheduled_matrix(config, matrix, continuation=None):
    """One frozen study; launch segments select work, never rewrite workloads."""
    segment=config.get('execution_segment','all')
    if segment not in ('all','thesis','diagnostics'):raise ValueError('unknown execution segment')
    complete={row['id'] for row in (continuation or {}).get('records',[]) if row.get('retry_policy_complete')}
    return [row for row in matrix if row['id'] not in complete and
            (segment=='all' or (row['phase'] in ('smoke','baseline'))==(segment=='thesis'))]


def planning_forecast(config, matrix, continuation=None):
    """Explicit conservative estimates, never invented from a successful readiness receipt."""
    matrix=scheduled_matrix(config,matrix,continuation)
    estimates=config.get('readiness',{}).get('phase_estimates_seconds',{})
    split=any(key in estimates for key in ('readiness_export','window_export','checkpoint_export','final_export'))
    required=('provision','rebuild','setup','capture_setup_restore','history','retry_reserve')+(
        ('readiness_export','window_export','checkpoint_export','final_export') if split else ('export',))
    missing=[key for key in required if type(estimates.get(key)) not in (int,float)
             or not math.isfinite(estimates[key]) or estimates[key]<=0]
    if missing:return {'fits':False,'seconds':None,'missing':missing,'reason':'reviewed phase timing estimates required; no local benchmark required'}
    groups=sum(1 for i,row in enumerate(matrix) if i==0 or (row['repeat'],row['cell'])!=(matrix[i-1]['repeat'],matrix[i-1]['cell']) or row['phase'] in ('arbitration','restart'))
    rebuild=estimates['rebuild'] if len({row['repeat'] for row in matrix})>1 else 0
    history=estimates['history'] if any(row.get('history_observation',True) and row['repeat']==1 and row['cell']=='boutique-mesh' and row['phase']=='baseline' for row in matrix) and not (continuation or {}).get('history') else 0
    resource_preparation=estimates.get('resource_prepare',0)
    if type(resource_preparation) not in (int,float) or not math.isfinite(resource_preparation) or resource_preparation<0:raise ValueError('invalid resource preparation allowance')
    resource_preparation*=sum(row['phase']=='resource' for row in matrix)
    readiness_export=estimates['readiness_export'] if split else estimates['export']
    window_export=estimates['window_export'] if split else estimates['export']
    checkpoints=len(matrix)+(1 if history else 0)+1
    checkpoint_seconds=checkpoints*estimates['checkpoint_export'] if split else 0
    final_export=estimates['final_export'] if split else 0
    authorization_bootstrap=600*len({row['repeat'] for row in matrix}) if config.get('execution_profile')=='thesis-robustness-full-v1' else 0
    seconds=(estimates['provision']+rebuild+resource_preparation+authorization_bootstrap+groups*(estimates['setup']+120+readiness_export)
             +sum(row['duration_seconds']+60+90+180+estimates['capture_setup_restore']+window_export for row in matrix)
             +history+estimates['retry_reserve']+config['limits']['cleanup_reserve_seconds']+checkpoint_seconds+final_export)
    return {'fits':seconds<=config['limits']['infrastructure_seconds'],'seconds':seconds,
            'export_model':'split-v1' if split else 'legacy-combined','authorization_bootstrap_seconds':authorization_bootstrap,
            'checkpoint_count':checkpoints if split else None,'checkpoint_export_seconds':checkpoint_seconds,
            'final_export_seconds':final_export,'readiness_export_seconds':groups*readiness_export,
            'window_export_seconds':len(matrix)*window_export,'estimates':estimates,'setup_groups':groups,'launch_segment':config.get('execution_segment','all'),'launch_windows':len(matrix),'rebuild_seconds':rebuild,'history_seconds':history,'resource_preparation_seconds':resource_preparation,'retry_reserve_policy':'bounded; deadline may leave retries or windows unattempted',
            'basis':'reviewed archived phase estimates plus frozen pre-roll/drain and quiet-counter allowance'}


def validate_launch(ctx):
    from .models import validate_schedule
    from .readiness import validate_cost
    if validate_schedule(ctx.manifest) not in RECOVERABLE_SCHEDULES:raise ValueError('v4 launch requires exact versioned schedule')
    if ctx.manifest.get('readiness_check') is not None:raise ValueError('legacy readiness gate must not be active in v4')
    if ctx.manifest.get('minimal_readiness')!={'version':4,'mode':'per-cell-read-only','observation_seconds':120,'traffic':False}:
        raise ValueError('minimal readiness contract missing or changed')
    forecast=planning_forecast(ctx.config,ctx.manifest['matrix'],ctx.manifest.get('continuation'))
    if not forecast['fits'] or forecast!=ctx.manifest.get('time_forecast'):raise ValueError('reviewed complete time forecast missing, changed or exceeds budget')
    if ctx.config.get('execution_scope','qualification')!='qualification':raise ValueError('v4 is per-cell readiness followed by measurement; readiness-only scope retired')
    validate_cost(ctx)
    from .models import validate_v2_inputs
    validate_v2_inputs(ctx.manifest,ctx.run_id)
    if ctx.manifest.get('schedule') in (*REPAIR_SCHEDULES,*COMPLETION_SCHEDULES):
        from .repair import validate_budget
        validate_budget(ctx.manifest)
    if ctx.manifest.get('schedule') in COMPLETION_SCHEDULES and ctx.config.get('execution_segment','all')!='all':
        raise ValueError('completion requires the entire frozen matrix')
    study=ctx.manifest.get('study_budget',{})
    if ctx.config.get('execution_segment','all')!='all':
        allocations=study.get('launch_allocations_usd',{})
        if study.get('gross_limit_usd')!=100 or set(allocations)!= {'thesis','diagnostics'} or any(type(x) not in (int,float) or not math.isfinite(x) or x<0 or x>80 for x in allocations.values()) or sum(allocations.values())>100:
            raise ValueError('split launches require reviewed allocations within USD80 each / USD100 combined')
        if study != ctx.config.get('readiness',{}).get('study_budget'):raise ValueError('study budget binding differs from config')
        if ctx.manifest['cost_preview']['gross_estimate']>allocations[ctx.config['execution_segment']]:raise ValueError('launch estimate exceeds its combined-study allocation')
    if ctx.config.get('execution_segment')=='diagnostics':
        prior=ctx.manifest.get('continuation')
        accounted={row['id'] for row in (prior or {}).get('records',[]) if row.get('retry_policy_complete')}
        required={row['id'] for row in ctx.manifest['matrix'] if row['phase'] in ('smoke','baseline')}
        if not prior or not required<=accounted or prior.get('history') is None:raise ValueError('diagnostic continuation requires thesis windows and history accounted for')
    from .thesis import local_contract
    local_contract(ctx.manifest.get('local_contract_evidence'),ctx.manifest['product_pins'])
    continuation=ctx.manifest.get('continuation')
    if ctx.manifest.get('schedule') in REPAIR_SCHEDULES and not ctx.manifest.get('origin_evidence',{}).get('report_sha256'):
        raise ValueError('repair origin evidence missing')
    if ctx.manifest.get('schedule') in REPAIR_SCHEDULES and ctx.config.get('execution_segment','all')!='all':
        raise ValueError('repair validation runs its complete six-window matrix; split study segments are unsupported')
    if ctx.manifest.get('schedule') in REPAIR_SCHEDULES and continuation:
        raise ValueError('repair validation must not import predecessor passes as continuation')
    if continuation:
        if continuation.get('binding')!=binding(ctx.manifest):raise ValueError('continuation changed protocol, product, scoring or implementation')
        if not continuation.get('export',{}).get('verified'):raise ValueError('continuation needs verified immutable predecessor checkpoint')


def binding(manifest):
    return canonical_sha({key:manifest.get(key) for key in ('protocol_definition','product_pins','image_lock','thresholds','implementation_sha256','study_budget')})


def _budget(ctx, seconds=0):
    if time.time()+seconds+ctx.config['limits']['cleanup_reserve_seconds']>=ctx.deadline:
        raise SafetyFailure('deadline or reserved cleanup budget exhausted')


def _json(ctx,*args):return ctx.json(*args)


def _generator_barrier(ctx,driver,spec,path,report):
    """No new generator until the preceding owned Job has disappeared."""
    job_file=path/'job.json'
    if not job_file.exists():return {'safe':True,'state':'not-created','artifacts_verified':False}
    expected=read(job_file);name=expected['metadata']['name'];ns=expected['metadata']['namespace']
    response=ctx.kube('get','job',name,'-n',ns,'--ignore-not-found','-o','json',timeout=15,check=False,cleanup=True)
    if response.returncode:raise SafetyFailure('generator absence could not be verified')
    if not response.stdout.strip():
        # Foreground/background Job deletion acknowledgements alone are not
        # proof that its Pods have stopped producing traffic.
        deadline=min(time.monotonic()+120,time.monotonic()+max(0,ctx.deadline-time.time()-ctx.config['limits']['cleanup_reserve_seconds']))
        while True:
            pods=json.loads(ctx.kube('get','pods','-n',ns,'-l','job-name='+name,'-o','json',timeout=15,cleanup=True).stdout).get('items',[])
            if not pods:break
            if time.monotonic()>=deadline:raise SafetyFailure('generator Pods remain after Job disappearance')
            time.sleep(min(1,max(0,deadline-time.monotonic())))
        return {'safe':True,'state':'job-and-pods-absent','artifacts_verified':read(path/'manifest.json').get('generator_artifacts_complete',False) if (path/'manifest.json').exists() else False}
    current=json.loads(response.stdout)
    saved=read(path/'generator-job-status.json') if (path/'generator-job-status.json').exists() else {}
    uid=saved.get('metadata',{}).get('uid')
    if not uid or current['metadata'].get('uid')!=uid:raise SafetyFailure('generator UID ownership cannot be established')
    for key,value in expected['metadata'].get('labels',{}).items():
        if current['metadata'].get('labels',{}).get(key)!=value:raise SafetyFailure('generator ownership labels changed')
    # Existing unverified artifact recovery is retained; do not issue an ACK.
    manifest=read(path/'manifest.json') if (path/'manifest.json').exists() else {}
    verified=manifest.get('generator_artifacts_complete') is True
    if not verified:driver.failure_hold(spec,report)
    # Generator Jobs use the legacy workload owner label; delete with exact UID
    # precondition through the API rather than guessing the diagnostic labels.
    raw=ctx.kube('get','pods','-n',ns,'-l','job-name='+name,'-o','json',timeout=15,cleanup=True)
    pods=json.loads(raw.stdout).get('items',[])
    for pod in pods:
        owners=pod.get('metadata',{}).get('ownerReferences',[])
        if not any(o.get('uid')==uid and o.get('kind')=='Job' for o in owners):raise SafetyFailure('generator Pod owner differs from saved Job UID')
    options={'apiVersion':'v1','kind':'DeleteOptions','propagationPolicy':'Foreground','preconditions':{'uid':uid,'resourceVersion':current['metadata']['resourceVersion']}}
    ctx.kube('delete','--raw','/apis/batch/v1/namespaces/'+ns+'/jobs/'+name,'-f','-',input_text=json.dumps(options),cleanup=True)
    ctx.kube('wait','--for=delete','job/'+name,'-n',ns,'--timeout=120s',timeout=130,cleanup=True)
    remaining=json.loads(ctx.kube('get','pods','-n',ns,'-l','job-name='+name,'-o','json',cleanup=True,timeout=15).stdout).get('items',[])
    if any(any(o.get('uid')==uid for o in p.get('metadata',{}).get('ownerReferences',[])) for p in remaining):raise SafetyFailure('generator-owned Pods remain after Job deletion')
    return {'safe':True,'state':'removed','uid':uid,'artifacts_verified':verified,'recovery_hold':not verified}


def transition(ctx,driver,spec,report):
    """Condition-based completion, also used after an incomplete measurement."""
    path=driver.paths.get(spec['id'])
    if path is None:
        path=ctx.result/'failed-windows'/spec['id'];path.mkdir(parents=True,exist_ok=True);driver.paths[spec['id']]=path
    result={'attempt_id':spec['id'],'logical_id':spec['logical_id'],'started_at':time.time(),'conditions':{},'safe':False}
    try:
        from .provision import require_context,assert_project_lease
        require_context(ctx);assert_project_lease(ctx,cleanup=True)
        from .. import runner as r
        namespace=ctx.kube('get','namespace','karthos-system','--ignore-not-found','-o','json',check=False,cleanup=True,timeout=15)
        if namespace.returncode:raise SafetyFailure('cluster API unavailable for safe restoration check')
        if namespace.stdout.strip():
            try:
                from .setup import verify_namespace_ownership
                # GKE namespace ownership differs from the local marker/PVC
                # contract. Validate both without weakening either guard.
                r.check_ownership(False,check_namespaces=False)
                snapshot_path=Path(driver.setup_path)/'namespace-ownership.json' if getattr(driver,'setup_path',None) else None
                snapshot=read(snapshot_path) if spec.get('setup_completed') and snapshot_path and snapshot_path.exists() else None
                if snapshot:
                    ownership=verify_namespace_ownership(ctx,snapshot['namespace_uids'],expected_uids=snapshot['namespace_uids'],marker_required=True,expected_marker_uid=snapshot['marker']['uid'])
                else:
                    ownership=verify_namespace_ownership(ctx,[r.KARTHOS_NAMESPACE,*r.NAMESPACES.values(),'istio-system'],expected_uids={},allow_absent=True)
            except Exception as exc:raise SafetyFailure('deployment ownership verification failed: '+str(exc)) from exc
        else:
            from .setup import verify_namespace_ownership
            ownership=verify_namespace_ownership(ctx,[r.KARTHOS_NAMESPACE,*r.NAMESPACES.values(),'istio-system'],expected_uids={},allow_absent=True)
        result['conditions']['ownership']=ownership
        result['conditions']['generator']=_generator_barrier(ctx,driver,spec,path,report)
        timing=read(path/'manifest.json') if (path/'manifest.json').exists() else {}
        capture=read(path/'structured/status.json') if (path/'structured/status.json').exists() else timing.get('structured_capture')
        # If startup never created instrumentation, no restoration is required.
        if (path/'structured').exists():
            if not isinstance(capture,dict):raise SafetyFailure('capture restoration status missing')
            if capture.get('cleanup',{}).get('restored') is not True:raise SafetyFailure('instrumentation restoration not verified')
            if not capture.get('workers_stopped') or not capture.get('proxy_stopped'):raise SafetyFailure('capture workers or proxy termination not verified')
        result['conditions']['capture']={'safe':True,'restored':not capture or capture.get('cleanup',{}).get('restored') is True,
                                         'measurement_errors':capture.get('errors',[]) if capture else []}
        diagnostics=read(path/'diagnostic-cleanup.json') if (path/'diagnostic-cleanup.json').exists() else None
        if diagnostics is not None and diagnostics.get('valid') is not True:raise SafetyFailure('helper cleanup incomplete')
        result['conditions']['helpers']={'safe':True,'cleanup':diagnostics}
        # Correct generation/readiness is checked again before reuse; a failed
        # check can trigger setup recovery without invalidating this export.
        result['conditions']['next_setup']={'state':'required-before-next-attempt'}
        result['safe']=True
    except Exception as exc:
        result['error']={'type':type(exc).__name__,'message':str(exc)}
    result['finished_at']=time.time();ctx.write(path/'transition.json',result)
    return result


def _retained_failure_report(path):
    """Recover bounded metadata, never re-decode a large report after an error."""
    from .report_summary import load_summary
    path=Path(path);result=load_summary(path)
    result.setdefault('execution',{'state':'incomplete'})
    result.setdefault('evidence',{'valid':False})
    detailed=path/'gke-evaluation.json'
    if detailed.is_file():
        result['retained_detail_reference']={'name':detailed.name,'sha256':sha(detailed),'bytes':detailed.stat().st_size}
    return result


def _persist_attempt_report(ctx,path,report,*,preserve_detail=False):
    # A bounded recovery view is an interpretation, never a replacement for
    # already-recorded detailed measurements.
    name='recovery-verdict.json' if preserve_detail and (Path(path)/'gke-evaluation.json').exists() else 'gke-evaluation.json'
    ctx.write(Path(path)/name,report)


def _checkpoint(ctx,records,history,readiness,sequence):
    from . import export
    directory=ctx.result/'checkpoints'/f'phase-{sequence:04d}';directory.mkdir(parents=True,exist_ok=False)
    reports={}
    for row in records:
        if row.get('path'):
            from .report_summary import load_summary
            reports[row['id']]=load_summary(row['path'])
    document={'version':4,'run_id':ctx.run_id,'binding':binding(ctx.manifest),'records':records,'history':history,
              'readiness':readiness,'reports':reports,'cluster_instances':ctx.state.get('evaluation_cluster_instances',[]),
              'sequence':sequence,'created_at':time.time(),'study_usage':{key:ctx.state.get(key,0) for key in ('cumulative_raw_evidence_bytes','uploaded_evidence_bytes')},'study_budget':ctx.manifest.get('study_budget'),'execution_segment':ctx.config.get('execution_segment','all'),'predecessor':ctx.manifest.get('continuation',{}).get('export')}
    # Retain graph evidence needed for cross-deployment comparisons as well as
    # numerical reports, so a continuation never needs the previous VM.
    for row in records:
        if row.get('path'):
            for name in ('api-nodes.json','api-edges.json','versions-after.json','consumer/ledger.json'):
                src=Path(row['path'])/name
                if src.exists():
                    target=directory/'retained'/row['id']/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,target)
    from .failure_evidence import retain_attempt_reports
    document['attempt_report_retention']=retain_attempt_reports(ctx,records,directory)
    ctx.write(directory/'checkpoint.json',document)
    from .quarantine import from_context
    receipt=export.upload_bundle(ctx,directory,category='checkpoint',quarantine=from_context(ctx),
        bucket=ctx.state['terraform']['foundation']['evidence_bucket'],
        prefix='gke-qualification-v1/runs/'+ctx.run_id+'/checkpoints/'+directory.name)
    if receipt.get('verified') is not True:raise SafetyFailure('phase checkpoint readback failed')
    receipt['checkpoint_sha256']=receipt['members']['checkpoint.json']['sha256']
    receipt['seal_sha256']=receipt['members']['GKE-SHA256SUMS']['sha256']
    ctx.write(ctx.result/'checkpoint-receipts'/f'phase-{sequence:04d}.json',receipt)
    return receipt


def evidence_failure_reason(report):
    """Explain invalid execution without promoting numerical mismatches to failures."""
    if report.get('reason'):return report['reason']
    if report.get('timing_error'):return report['timing_error']
    evidence=report.get('evidence',{})
    reasons=[]
    for key in ('agent_identities','extended_capture'):
        component=evidence.get(key,{})
        if isinstance(component,dict):
            for error in component.get('errors',[])[:5]:
                reasons.append(key+': '+str(error)[:500])
    for error in evidence.get('reference_errors',[])[:5]:
        reasons.append('reference: '+str(error)[:500])
    if reasons:return '; '.join(reasons)[:2000]
    if evidence.get('valid') is not True:return 'required evidence invalid; inspect per-boundary report'
    if report.get('execution',{}).get('state')!='complete':return 'execution incomplete; inspect attempt diagnostics'
    return None


def attempt_record(spec,report,*,failure=None,barrier=None,receipt=None,retry_policy_complete=False):
    """Physical measurements survive failures of later continuation/export steps."""
    report=report or {}
    generator=report.get('traffic',{}).get('report',{}).get('generator_contract',{}).get('retained_generator',{}) or {}
    unexpected=generator.get('unexpected_outcomes')
    execution=report.get('execution',{}).get('state','unattempted')
    valid=report.get('evidence',{}).get('valid') is True
    failed=bool(failure or execution!='complete' or not valid or type(unexpected) is int and unexpected>0)
    return {'id':spec['logical_id'],'origin_id':spec.get('origin_id'),'attempt_id':spec['id'],'state':'failed' if failed else 'complete',
            'execution':execution,'evidence_valid':valid,'evidence_scope':'local measurement; export and continuation reported separately',
            'application_outcomes':'unknown' if unexpected is None else ('unexpected ('+str(unexpected)+')' if unexpected else 'no unexpected outcomes'),
            'numerical_agreement':report.get('numerical_agreement',{}).get('state','insufficient'),
            'families':report.get('families',{}),'analysis':report.get('analysis',{}),
            'reason':failure or evidence_failure_reason(report),'path':report.get('path'),
            'export':receipt or {'verified':False},'evidence_export_verified':(receipt or {}).get('verified') is True,
            'transition':barrier,'continuation_safe':barrier.get('safe') if isinstance(barrier,dict) else None,
            'retry_policy_complete':retry_policy_complete}


def _row(spec,attempts):
    # First valid observation wins, not the best numerical score. Every attempt
    # remains separately inspectable; retry cannot erase a valid disagreement.
    chosen=next((a for a in attempts if a.get('execution')=='complete' and a.get('evidence_valid') and a.get('export',{}).get('verified') is True),attempts[-1])
    return {**chosen,'id':spec['id'],'logical_id':spec['id'],'attempts':attempts,
            'first_attempt_state':attempts[0]['state'],'attempt_count':len(attempts),
            'retried':len(attempts)>1,'representative_attempt':chosen.get('attempt_id'),
            'retry_policy_complete':attempts[-1].get('retry_policy_complete',False)}


def execute(ctx,driver):
    from . import export
    from .minimal_readiness import check_cell, MinimalReadinessSafetyError
    from .execution_diagnostics import ExecutionDiagnostics
    matrix=ctx.manifest['matrix'];launch_matrix=scheduled_matrix(ctx.config,matrix,ctx.manifest.get('continuation'));records=[];history=None;readiness=[];failure=None;cleanup=None;sequence=0
    current_attempt=None
    previous=ctx.manifest.get('continuation') or {}
    if previous:
        ctx.state['evaluation_cluster_instances']=copy.deepcopy(previous.get('cluster_instances',[]))
        for key,value in previous.get('study_usage',{}).items():
            if key in ('cumulative_raw_evidence_bytes','uploaded_evidence_bytes'):ctx.state[key]=value
        records=copy.deepcopy(previous['records']);history=previous.get('history');readiness=copy.deepcopy(previous.get('readiness',[]))
        for row in records:
            target=ctx.result/'predecessor-reports'/row['id'];target.mkdir(parents=True,exist_ok=True)
            if row['id'] in previous.get('reports',{}):ctx.write(target/'orchestration-summary.json',previous['reports'][row['id']])
            for name,value in previous.get('retained_graphs',{}).get(row['id'],{}).items():ctx.write(target/name,value)
            row['predecessor_path']=row.get('path');row['path']=str(target)
            retained=target/'orchestration-summary.json'
            if retained.exists():
                document=read(retained);document['path']=str(target);ctx.write(retained,document)
    pending_attempts={row['id']:copy.deepcopy(row.get('attempts',[])) for row in records if not row.get('retry_policy_complete')}
    done={row['id'] for row in records if row.get('retry_policy_complete')};records=[row for row in records if row['id'] in done];active_repeat=None;active_cell=None;blocked_repeat=None;arbitration_ready=False;cell_readiness={}
    try:
        for spec in launch_matrix:
            if spec['id'] in done:continue
            _budget(ctx)
            if spec['repeat']==blocked_repeat:
                records.append({'id':spec['id'],'state':'blocked','execution':'unattempted','evidence_valid':False,'reason':'cluster establishment failed twice','attempts':[]});continue
            if spec['repeat']!=active_repeat:
                cluster_errors=[]
                for number in (1,2):
                    try:
                        if previous and spec['repeat']==2 and not ctx.state.get('cluster_generation'):
                            ctx.state['cluster_generation']=1  # logical predecessor; no foreign cloud UID is adopted
                        driver.start_repeat(spec['repeat']);active_repeat=spec['repeat'];active_cell=None
                        cluster={'repeat':active_repeat,'run_id':ctx.run_id,'observed_at':time.time(),
                                 'resources':copy.deepcopy([r for r in ctx.state.get('resources',[]) if r['kind']=='cluster']),
                                 'nodes':copy.deepcopy(driver.nodes)}
                        ctx.state.setdefault('evaluation_cluster_instances',[]).append(cluster);ctx.event('phase-cluster-established',cluster=cluster)
                        break
                    except (SafetyFailure,ProductDefect,KeyboardInterrupt):raise
                    except Exception as exc:
                        cluster_errors.append({'attempt':number,'error':str(exc),'at':time.time()});ctx.write(ctx.result/'infrastructure'/f'repeat-{spec["repeat"]}-attempt-{number}.json',cluster_errors[-1])
                        if is_authorization_failure(exc):
                            raise SafetyFailure('cluster authorization failure; unchanged retry forbidden') from exc
                        from .teardown import destroy_cluster
                        restored=destroy_cluster(ctx)
                        if restored.get('residual') or restored.get('errors') or restored.get('unresolved_creation_intents'):raise SafetyFailure('cluster creation recovery left resources') from exc
                else:
                    blocked_repeat=spec['repeat'];failure={'kind':'cluster-unavailable','errors':cluster_errors}
                    # No repeat can progress without this cluster. Seal below.
                    break
            if spec['phase']=='arbitration' and spec['scenario']=='replanned-trace' and not arbitration_ready:
                predecessor_both=next((row for row in records if any(m['id']==row['id'] and m['phase']=='arbitration' and m['scenario']=='both-sources' for m in matrix) and row.get('evidence_valid')),None)
                if previous and predecessor_both:
                    prerequisite=next(row for row in matrix if row['phase']=='arbitration' and row['scenario']=='both-sources')
                    rebuilt={**prerequisite,'id':spec['id']+'-resume-prerequisite'}
                    driver.setup(rebuilt)
                    ready=check_cell(ctx,driver,rebuilt,rebuilt['id']);ready['repeat']=spec['repeat'];readiness.append(ready)
                    ctx.write(ctx.result/'infrastructure/arbitration-resume-prerequisite.json',{'readiness':ready,'predecessor_window':predecessor_both['id'],'interpretation':'new operational prerequisite; no old traffic or live continuity claimed'})
                    arbitration_ready=ready.get('usable') is True
                if not arbitration_ready:
                    records.append({'id':spec['id'],'state':'blocked','execution':'unattempted','evidence_valid':False,'reason':'prior both-sources stage unavailable','attempts':[]});continue
            attempts=pending_attempts.get(spec['id'],[])
            for number in range(len(attempts)+1,3):
                _budget(ctx,spec['duration_seconds'])
                attempt={**spec,'id':spec['id']+f'-a{number}','logical_id':spec['id'],'attempt_number':number}
                current_attempt=attempt
                report=None;error=None;setup_ok=False;barrier=None;receipt=None
                attempt['setup_completed']=False
                path=ctx.result/'attempt-diagnostics'/attempt['id']
                diagnostic=ExecutionDiagnostics(ctx,path,namespaces=['karthos-system',__import__('evaluation.protocol',fromlist=['NAMESPACES']).NAMESPACES[spec['demo']]])
                try:
                    diagnostic.start()
                    key=(spec['repeat'],spec['cell'])
                    must_setup=active_cell!=key or number==2 or spec['phase']=='restart' or spec['phase']=='arbitration'
                    if must_setup:
                        from .provision import require_context, assert_project_lease
                        require_context(ctx);assert_project_lease(ctx)
                        # Replanned-trace recovery must preserve both-sources
                        # continuity; it is not a generic reset/redeployment.
                        driver.setup(attempt);active_cell=key
                    attempt['setup_completed']=True
                    if must_setup or key not in cell_readiness:
                        ready=check_cell(ctx,driver,attempt,attempt['id']);ready['repeat']=spec['repeat'];readiness.append(ready);cell_readiness[key]=ready
                    else:ready=cell_readiness[key]
                    if not ready.get('usable'):raise RuntimeError('minimal readiness essential checks unavailable')
                    setup_ok=True
                    ctx.event('window-start',window=attempt['id'],logical_id=spec['id'],repeat=spec['repeat'])
                    report=driver.window(attempt)
                except (SafetyFailure,ProductDefect,MinimalReadinessSafetyError,KeyboardInterrupt):raise
                except Exception as exc:
                    error={'type':type(exc).__name__,'message':str(exc),'traceback':traceback.format_exc()}
                    if is_authorization_failure(exc):error['nonretryable_authorization']=True
                    ctx.write(path/'failure.json',error);active_cell=None
                finally:
                    diagnostics_status=diagnostic.stop()
                    if not setup_ok:
                        from .failure_evidence import retain_setup_evidence
                        retain_setup_evidence(ctx,driver,attempt,path/'setup')
                if not diagnostics_status.get('stopped') is True:raise SafetyFailure('supplementary diagnostic worker remains active')
                retained=driver.paths.get(attempt['id'])
                preserve_detail=bool(report is None and retained and (Path(retained)/'gke-evaluation.json').exists())
                if preserve_detail:report=_retained_failure_report(retained)
                report=report or {'execution':{'state':'incomplete' if setup_ok else 'unattempted'},'evidence':{'valid':False},'reason':error and error['message']}
                window_path=driver.paths.get(attempt['id'])
                if window_path is None:
                    window_path=ctx.result/'failed-windows'/attempt['id'];window_path.mkdir(parents=True,exist_ok=True);driver.paths[attempt['id']]=window_path
                shutil.copytree(path,window_path/'execution-diagnostics',dirs_exist_ok=True)
                report['path']=str(window_path);report['spec']=attempt
                _persist_attempt_report(ctx,window_path,report,preserve_detail=preserve_detail)
                from .report_summary import save_summary
                from .evidence_first import enabled
                deferred=enabled(ctx) and report.get('analysis',{}).get('state')=='pending'
                if not deferred:save_summary(window_path,report)
                barrier=transition(ctx,driver,attempt,report)
                if not barrier['safe']:
                    # Only immutable, stopped supplementary diagnostics enter
                    # final-summary. Do not archive mutable capture streams.
                    ctx.write(ctx.result/'failure-traces'/(attempt['id']+'.json'),{'barrier':barrier,'report':report,'diagnostics':str(path)})
                    raise SafetyFailure('attempt completion barrier failed: '+str(barrier.get('error')))
                from .timing import timed
                with timed(ctx,'window-export',attempt['id']) as export_timing:
                    receipt=driver.upload(attempt,report)
                receipt['export_timing']=dict(export_timing)
                if receipt.get('verified') is not True:raise SafetyFailure('attempt evidence export did not verify')
                analysis_failed=False
                if deferred:
                    raw_receipt=receipt
                    report=driver.analyse(attempt,raw_receipt)
                    analysis_failed=report.get('analysis',{}).get('state')=='failed'
                    analysis_receipt=driver.upload_analysis(attempt,report)
                    if analysis_receipt.get('verified') is not True:raise SafetyFailure('analysis evidence export did not verify')
                    receipt=dict(raw_receipt,analysis_export=analysis_receipt)
                    report['analysis_export']=analysis_receipt
                    if report.get('source_report'):
                        report['source_report']=dict(report['source_report'],name='analysis/'+report['source_report']['name'])
                    # Preserve raw measurement checkpoint; expose the bounded
                    # analysis to checkpoint/history consumers without expansion.
                    ctx.write(window_path/'orchestration-summary.json',report)
                generator=report.get('traffic',{}).get('report',{}).get('generator_contract',{}).get('retained_generator',{}) or {}
                unexpected=generator.get('unexpected_outcomes')
                valid=report.get('evidence',{}).get('valid') is True
                execution=report.get('execution',{}).get('state','incomplete')
                failed=bool(error or execution!='complete' or not valid or type(unexpected) is int and unexpected>0)
                nonretryable_authorization=bool(error and error.get('nonretryable_authorization'))
                item=attempt_record(attempt,report,failure=error,barrier=barrier,receipt=receipt,retry_policy_complete=number==2 or not failed or analysis_failed or nonretryable_authorization)
                item['cluster']=copy.deepcopy(ctx.state.get('evaluation_cluster_instances',[])[-1:])
                attempts.append(item)
                row=_row(spec,copy.deepcopy(attempts))
                present=next((i for i,r in enumerate(records) if r['id']==spec['id']),None)
                if present is None:records.append(row)
                else:records[present]=row
                export.render_matrix(ctx.result,matrix,records)
                sequence+=1;row['checkpoint']=_checkpoint(ctx,records,history,readiness,sequence)
                if not barrier['safe']:raise SafetyFailure('attempt completion barrier failed: '+str(barrier.get('error')))
                if spec['phase']=='arbitration' and spec['scenario']=='both-sources':arbitration_ready=setup_ok and valid
                ctx.event('window-exported',window=attempt['id'],logical_id=spec['id'],state=item['state'])
                if nonretryable_authorization:
                    raise SafetyFailure('deployment authorization failure; evidence preserved, unchanged retry forbidden')
                if ctx.config.get('execution_profile') in ('boutique-complement-v1','thesis-robustness-full-v1') and failed and (number==2 or analysis_failed):
                    raise SafetyFailure('bounded profile operational attempts exhausted; evidence preserved, remaining windows unattempted')
                if not failed or analysis_failed:break
                # Do not immediately repeat explicit rediscovery against the
                # already transitioned plan. Reconstruct its prerequisite first.
                if number==1 and spec['phase']=='arbitration' and spec['scenario']=='replanned-trace':
                    prerequisite=next(r for r in matrix if r['phase']=='arbitration' and r['scenario']=='both-sources')
                    driver.setup({**prerequisite,'id':attempt['id']+'-prerequisite'});active_cell=None
            if ctx.manifest.get('schedule') not in REPAIR_SCHEDULES and spec.get('history_observation',True) and spec['repeat']==1 and spec['cell']=='boutique-mesh' and spec['phase']=='baseline' and history is None and active_cell is None:
                history={'state':'blocked','reason':'Boutique deployment unavailable after baseline attempts','action_taken':False}
                sequence+=1;_checkpoint(ctx,records,history,readiness,sequence)
            if ctx.manifest.get('schedule') not in REPAIR_SCHEDULES and spec.get('history_observation',True) and spec['repeat']==1 and spec['cell']=='boutique-mesh' and spec['phase']=='baseline' and history is None and active_cell is not None:
                history_errors=[];history_attempts=[]
                full_history=ctx.config.get('execution_profile')=='thesis-robustness-full-v1'
                for number in (1,2):
                    try:
                        _budget(ctx,720);history=driver.thesis_history({**spec,'id':spec['id']+f'-history-a{number}'})
                        if full_history:history_attempts.append({'attempt':number,'report':copy.deepcopy(history)})
                        if full_history and history.get('nonretryable_authorization') is True:
                            history['attempts']=history_attempts
                            sequence+=1;_checkpoint(ctx,records,history,readiness,sequence)
                            raise SafetyFailure('history authorization failure; preserved report, unchanged retry forbidden')
                        if history.get('export',{}).get('verified') is not True:raise SafetyFailure('history export failed')
                        if history.get('continuation_safe') is not True:raise SafetyFailure('history cleanup not verified')
                        if full_history and history.get('answer',{}).get('state') not in ('correct','incorrect'):
                            continue
                        break
                    except (SafetyFailure,ProductDefect,KeyboardInterrupt):raise
                    except Exception as exc:
                        history_errors.append({'attempt':number,'error':str(exc)})
                        if full_history:history_attempts.append(history_errors[-1])
                        if full_history and is_authorization_failure(exc):
                            history_errors[-1]['nonretryable_authorization']=True
                            detail=dict(history_errors[-1],traceback=traceback.format_exc())
                            ctx.write(ctx.result/'failure-traces'/f'history-authorization-repeat-{spec["repeat"]}-a{number}.json',detail)
                            history={'state':'incomplete','attempts':history_attempts,'nonretryable_authorization':True}
                            sequence+=1;_checkpoint(ctx,records,history,readiness,sequence)
                            raise SafetyFailure('history authorization failure; diagnostics preserved, unchanged retry forbidden') from exc
                if history is None:history={'state':'incomplete','attempts':history_errors}
                if full_history:history['attempts']=history_attempts
                sequence+=1;_checkpoint(ctx,records,history,readiness,sequence)
                if full_history and history.get('answer',{}).get('state') not in ('correct','incorrect'):
                    raise SafetyFailure('full profile history operational attempts exhausted; evidence preserved, remaining windows unattempted')
    except (Exception,KeyboardInterrupt) as exc:
        failure={'kind':type(exc).__name__,'message':str(exc),'traceback':traceback.format_exc()}
        if current_attempt and not any(a.get('attempt_id')==current_attempt['id'] for row in records for a in row.get('attempts',[])):
            observed=report
            retained=driver.paths.get(current_attempt['id'])
            if observed is None and retained and (Path(retained)/'gke-evaluation.json').exists():observed=_retained_failure_report(retained)
            from .evidence_first import AnalysisSafetyError
            if isinstance(exc,AnalysisSafetyError):barrier={'safe':False,'error':failure}
            measurement_preserved=bool(receipt and receipt.get('verified') is True and observed and observed.get('analysis'))
            failed=attempt_record(current_attempt,observed,failure=failure,barrier=barrier,receipt=receipt,
                                  retry_policy_complete=measurement_preserved)
            if measurement_preserved:failed['recovery']='resume analysis/export from preserved raw evidence; no automatic traffic rerun'
            failed['path']=failed.get('path') or (str(retained) if retained else None)
            failed['diagnostics']=str(ctx.result/'attempt-diagnostics'/current_attempt['id'])
            at=next((i for i,row in enumerate(records) if row['id']==current_attempt['logical_id']),None)
            prior=records[at].get('attempts',[]) if at is not None else []
            row=_row({'id':current_attempt['logical_id']},prior+[failed])
            if at is None:records.append(row)
            else:records[at]=row
    finally:
        try:
            sequence+=1;_checkpoint(ctx,records,history,readiness,sequence)
        except Exception as exc:failure={'kind':'checkpoint-failed','message':str(exc),'prior':failure}
        try:
            cleanup=driver.finish()
            cluster_cleanup=cleanup.get('cluster_cleanup',{})
            if cluster_cleanup.get('residual') or cluster_cleanup.get('errors') or cluster_cleanup.get('unresolved_creation_intents'):
                failure={'kind':'cleanup-incomplete','details':cluster_cleanup,'prior':failure}
        except Exception as exc:
            cleanup={'error':str(exc),'verified':False};failure={'kind':'cleanup-failed','message':str(exc),'prior':failure}
    accounted={r['id'] for r in records}
    for spec in matrix:
        if spec['id'] not in accounted:records.append({'id':spec['id'],'state':'unattempted','execution':'unattempted','evidence_valid':False,'reason':failure or ('deferred to explicit diagnostic continuation' if ctx.config.get('execution_segment')=='thesis' and spec['phase'] not in ('smoke','baseline') else 'dependent work unavailable'),'attempts':[]})
    export.render_matrix(ctx.result,matrix,records)
    from .thesis import claim_matrix, required_claims_confirmed
    latest_readiness={}
    for item in readiness:latest_readiness[(item.get('repeat'),item.get('cell'))]=item
    readiness_report={'ready':bool(latest_readiness) and all(r.get('usable') for r in latest_readiness.values()),'cells':readiness,'mode':'per-cell-read-only'}
    claims=claim_matrix(ctx,records,history,readiness_report);ctx.write(ctx.result/'claim-matrix.json',claims)
    launch_ids={row['id'] for row in launch_matrix}
    segment_complete=not failure and all(row.get('retry_policy_complete') for row in records if row['id'] in launch_ids) and launch_ids<={row['id'] for row in records}
    if ctx.manifest.get('schedule') not in REPAIR_SCHEDULES and ctx.config.get('execution_segment')=='thesis' and history is None:segment_complete=False
    study_complete=segment_complete and all(row.get('retry_policy_complete') for row in records) and (history is not None or not history_required(ctx.manifest.get('schedule'),matrix))
    report={'protocol':ctx.manifest.get('schedule','thesis-qualification-v4'),'state':('complete' if study_complete else 'segment-complete') if segment_complete else 'incomplete','execution_segment':ctx.config.get('execution_segment','all'),'segment_complete':segment_complete,'study_complete':study_complete,'planned_launch_windows':len(launch_matrix),'study_budget':ctx.manifest.get('study_budget'),
            'planned_windows':len(matrix),'attempted_windows':sum(bool(r.get('attempts')) for r in records),'attempts':sum(len(r.get('attempts',[])) for r in records),
            'records':records,'windows':records,'history':history,'readiness':readiness_report,'failure':failure,'cleanup':cleanup,
            'qualified':study_complete and required_claims_confirmed(claims) if ctx.manifest.get('schedule')==COMPLETION_V2 else not failure and all(r['state'] not in ('unattempted','blocked') for r in records) and all(r['verdict'] in ('pass','not-applicable') for r in claims['claims']),
            'numerical_disagreement_is_result':True,'temporary_removal_confirmed':False,'claim_matrix':'claim-matrix.json'}
    ctx.write(ctx.result/'run-report.json',report);return report


def failed_staging(ctx,driver,reason):
    from . import export
    records=[{'id':r['id'],'state':'unattempted','execution':'unattempted','reason':reason} for r in ctx.manifest['matrix']]
    export.render_matrix(ctx.result,ctx.manifest['matrix'],records)
    try:cleanup=driver.finish()
    except Exception as exc:cleanup={'error':str(exc)}
    report={'protocol':ctx.manifest.get('schedule','thesis-qualification-v4'),'state':'incomplete','records':records,'windows':records,'failure':reason,'cleanup':cleanup,'qualified':False}
    ctx.write(ctx.result/'run-report.json',report);return report


def load_continuation(directory, receipt_path, manifest):
    from .export import verify
    directory=Path(directory).resolve();receipt=read(receipt_path)
    if not verify(directory)['valid']:raise ValueError('predecessor checkpoint seal invalid')
    if receipt.get('verified') is not True or not str(receipt.get('uri','')).startswith('gs://'):
        raise ValueError('verified predecessor export receipt required')
    if sha(directory/'checkpoint.json')!=receipt.get('checkpoint_sha256') or sha(directory/'GKE-SHA256SUMS')!=receipt.get('seal_sha256'):
        raise ValueError('predecessor export receipt/checkpoint checksum mismatch')
    document=read(directory/'checkpoint.json')
    if document.get('version')!=4 or document.get('binding')!=binding(manifest):
        raise ValueError('predecessor protocol/product/scoring/implementation differs')
    allowed={r['id'] for r in manifest['matrix']}
    records=document.get('records',[])
    if len({r['id'] for r in records})!=len(records) or any(r['id'] not in allowed for r in records):
        raise ValueError('predecessor contains duplicate or foreign windows')
    document['retained_graphs']={}
    for record in records:
        base=directory/'retained'/record['id']
        document['retained_graphs'][record['id']]={str(f.relative_to(base)):read(f) for f in base.rglob('*.json')}
    document['export']=receipt
    return document
