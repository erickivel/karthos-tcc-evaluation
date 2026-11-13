"""Serial 46-window GKE execution with injected orchestration tests.

Importing this module never provisions infrastructure. run(ctx) requires the
approved LiveContext and runs on the separate controller with an explicit target.
The controller survives cluster cleanup so the operator can verify/export and
perform final VM teardown. Expected disruption never turns missing evidence into
an accuracy or traffic-oracle pass.
"""
from __future__ import annotations
from .models import RECOVERABLE_SCHEDULES

import copy
import hashlib
import json
import math
from concurrent.futures import ThreadPoolExecutor
import shutil
import threading
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

from .models import QUALIFICATION,REHEARSAL,THESIS_SCHEDULES,validate_schedule,read,write,sha
from . import export as exports
from .timing import last_completed,timed
from ..protocol import Cell,source_id
from ..verify import parse_time


def capture_clocks(capture,generator_node=None):
    from ..timing_capture import clock_samples
    agents=[pod for key,pod in capture.pods.items() if key.split(':',1)[0]=='agent']
    if len(agents)==1 and 'agent' in capture.pods:
        return clock_samples(capture,node_name=agents[0]['spec']['nodeName'])
    names=[pod.get('spec',{}).get('nodeName') for pod in agents]
    if not generator_node or generator_node not in names or len(names)!=len(set(names)) or any(not name for name in names):
        raise ValueError('explicit generator node and one Agent per distinct node required for clock gate')
    clocks={node:clock_samples(capture,node_name=node,persistent=True) for node in sorted(names)}
    bounds=[clock.get('clock_uncertainty_seconds') for clock in clocks.values()]
    valid=all(clock.get('valid') is True for clock in clocks.values()) and all(isinstance(bound,(int,float)) and not isinstance(bound,bool) and 0<=bound<.25 for bound in bounds)
    return {'valid':valid,'node_clocks':clocks,'generator_node_name':generator_node,'required_nodes':sorted(names),
            'clock_uncertainty_seconds':max(bounds) if valid else None,
            'semantics':'every Agent node and explicitly placed generator node bracketed against controller clock'}


def execute_matrix(ctx,driver,*,gate=None):
    """Execute the frozen matrix serially; driver is the only external boundary.

    driver: start_repeat(int), setup(spec), window(spec)->report,
    upload(spec,report)->verified receipt, finish()->cleanup result.
    Numeric disagreement is retained. Missing normal-window evidence blocks later
    work; completed disruption windows retain an explicitly incomplete verdict.
    """
    schedule=validate_schedule(ctx.manifest)
    if schedule in RECOVERABLE_SCHEDULES:
        from .recoverable_run import execute
        return execute(ctx,driver)
    rehearsal=schedule==REHEARSAL
    matrix=ctx.manifest['matrix']
    readiness_only = ctx.config.get('execution_scope') == 'readiness-only'
    observation=ctx.manifest.get('verification_observation',{})
    verification=None
    if observation.get('mode')=='bounded execution verification':
        seconds=observation.get('seconds')
        if type(seconds) is not int or seconds<=0:
            raise ValueError('verification duration must be a positive integer')
        verification={'state':'incomplete','target_seconds':seconds,'first_measured_start':None,
                      'last_measured_end':None,'observed_elapsed_seconds':0}
    records=[];active_repeat=None;active_cell=None;failure=None;cleanup=None;smokes=set()
    readiness_report = None;history=None
    try:
        if ctx.manifest.get('readiness_check') and gate is None:
            raise ValueError('mandatory readiness gate missing')
        if gate is not None:
            readiness_report = gate(ctx, driver)
            if readiness_report.get('ready') is not True:
                raise ValueError('readiness blocked measurement: '+str(readiness_report.get('error')))
            active_repeat = 1  # The gate already preflighted this exact cluster.
        for spec in ([] if readiness_only else matrix):
            if time.time()+ctx.config['limits']['cleanup_reserve_seconds']>=ctx.deadline:raise TimeoutError('reserved cleanup budget reached')
            if spec['repeat']!=active_repeat:
                driver.start_repeat(spec['repeat']);active_repeat=spec['repeat'];active_cell=None
            key=(spec['repeat'],spec['cell'])
            if key!=active_cell or spec['phase']=='arbitration' or (rehearsal and spec['phase']=='restart'):
                with timed(ctx,'setup',spec['id']):
                    driver.setup(spec)
                active_cell=key
            if spec['phase']=='baseline' and (spec['repeat'],spec['cell'],spec['generator']) not in smokes:
                raise RuntimeError('required matching smoke gate unavailable')
            ctx.event('window-start',window=spec['id'],repeat=spec['repeat'])
            report=None;error=None
            try:report=driver.window(spec)
            except (Exception,KeyboardInterrupt) as exc:
                error=str(exc) or type(exc).__name__;report={'execution':{'state':'incomplete'},'evidence':{'valid':False},'state':'incomplete','reason':error}
                ctx.write(ctx.result/'failure-traces'/(spec['id']+'.json'),{'error_type':type(exc).__name__,'message':error,'traceback':traceback.format_exc(),'window':spec['id']})
            row={'id':spec['id'],'phase':spec['phase'],'state':'incomplete','evidence_valid':report.get('evidence',{}).get('valid') is True,
                 'execution':report.get('execution',{}).get('state','incomplete'),
                 'numerical_agreement':report.get('numerical_agreement',{}).get('state','insufficient'),
                 'reason':error or report.get('reason'),'path':report.get('path')}
            generator=report.get('traffic',{}).get('report',{}).get('generator_contract',{}).get('retained_generator',{})
            unexpected=generator.get('unexpected_outcomes')
            row['application_outcomes']=('unknown' if unexpected is None else
                                         'unexpected ('+str(unexpected)+')' if unexpected else 'no unexpected outcomes')
            records.append(row)
            try:
                receipt=driver.upload(spec,report);row['export']=receipt
                if receipt.get('verified') is not True:raise RuntimeError('window export did not verify')
            except Exception as exc:
                row['reason']='export: '+str(exc)
                if hasattr(driver,'failure_hold'):row['hold']=driver.failure_hold(spec,report)
                raise
            if error:
                if hasattr(driver,'failure_hold'):row['hold']=driver.failure_hold(spec,report)
                raise RuntimeError(error)
            if row['execution']!='complete':
                if hasattr(driver,'failure_hold'):row['hold']=driver.failure_hold(spec,report)
                raise RuntimeError('window execution incomplete: '+spec['id'])
            if not row['evidence_valid']:
                if (spec['phase']=='restart' or (not rehearsal and spec['phase']=='resource')) and report.get('traffic',{}).get('expected_disruption') is True and report.get('diagnostic_required_evidence',{}).get('valid',not rehearsal) is True:
                    row['state']='diagnostic-incomplete'
                    row['reason']=row['reason'] or 'disruption completed; required evidence remains incomplete'
                else:
                    if hasattr(driver,'failure_hold'):row['hold']=driver.failure_hold(spec,report)
                    raise RuntimeError('required window evidence unavailable: '+spec['id'])
            else:row['state']='diagnostic'
            if spec['phase']=='smoke':smokes.add((spec['repeat'],spec['cell'],spec['generator']))
            exports.render_matrix(ctx.result,matrix,records)
            if hasattr(driver,'checkpoint'):
                row['checkpoint']=driver.checkpoint(matrix,records)
                if row['checkpoint'].get('verified') is not True:raise RuntimeError('immutable matrix/progress checkpoint export did not verify')
            ctx.event('window-exported',window=spec['id'],evidence_valid=row['evidence_valid'],numerical_agreement=row['numerical_agreement'])
            if schedule in THESIS_SCHEDULES and spec['repeat']==1 and spec['cell']=='boutique-mesh' and spec['phase']=='baseline':
                with timed(ctx,'thesis-history',spec['id']):
                    history=driver.thesis_history(spec)
                if history.get('export',{}).get('verified') is not True:
                    raise RuntimeError('thesis history evidence export did not verify')
                if history.get('continuation_safe') is not True:
                    raise RuntimeError('thesis history ownership or cleanup did not verify')
            if verification is not None and row['evidence_valid']:
                start=parse_time(report['manifest']['start'])
                end=parse_time(report['manifest']['end'])
                if end<=start:
                    raise ValueError('verification requires valid measured window bounds')
                first=verification['first_measured_start'] or start.isoformat()
                elapsed=(end-parse_time(first)).total_seconds()
                verification.update(first_measured_start=first,last_measured_end=end.isoformat(),
                                    observed_elapsed_seconds=elapsed)
                if elapsed>=verification['target_seconds']:
                    verification['state']='complete'
                    ctx.event('verification-window-boundary-reached',window=spec['id'],observed_elapsed_seconds=elapsed)
                    break

    except (Exception,KeyboardInterrupt) as exc:failure=str(exc) or type(exc).__name__
    finally:
        try:
            with timed(ctx,'cluster-teardown'):
                cleanup=driver.finish()
            nested=cleanup.get('cluster_cleanup',{}) if isinstance(cleanup,dict) else {}
            if cleanup.get('errors') or cleanup.get('residual') or nested.get('errors') or nested.get('residual'):failure=failure or 'cleanup residuals or errors retained'
        except Exception as exc:cleanup={'errors':[str(exc)]};failure=failure or ('cleanup: '+str(exc))
        unattempted_reason=failure or ('bounded execution verification reached its observation target' if verification and verification['state']=='complete' else None)
        if readiness_only and not failure:
            unattempted_reason='readiness-only execution requested; measured windows intentionally not run'
        exports.render_matrix(ctx.result,matrix,records,unattempted_reason=unattempted_reason)
    complete=(readiness_report is not None and readiness_report.get('ready') is True if readiness_only else len(records)==len(matrix)) and not failure
    report={'protocol':'gke-qualification-v1','schedule':schedule,'state':'complete' if complete else 'incomplete','planned_windows':len(matrix),
            'executed_windows':len(records),'windows':records,'error':failure,'cleanup':cleanup,
            'qualified':schedule==QUALIFICATION and complete and all(row['evidence_valid'] for row in records),
            'numerical_disagreement_is_result':True,'controller_present_until_external_cleanup':True,'temporary_removal_confirmed':False}
    report['execution_scope']='readiness-only' if readiness_only else 'qualification'
    if readiness_only:
        report['planned_qualification_windows']=len(matrix)
        report['planned_windows']=0
        report['qualified']=False
    report['last_completed_phase']=last_completed(ctx.result/'phase-timings.ndjson')
    if schedule in THESIS_SCHEDULES:
        from .thesis import claim_matrix
        report['protocol']=schedule
        report['history']=history
        claim_report=claim_matrix(ctx,records,history,readiness_report)
        ctx.write(ctx.result/'claim-matrix.json',claim_report)
        report['claim_matrix']='claim-matrix.json'
        report['qualified']=not readiness_only and complete and all(row['verdict']=='pass' for row in claim_report['claims'])
    if readiness_report is not None:
        report['readiness']=readiness_report
    if verification is not None:
        if failure:verification['state']='incomplete'
        report['execution_verification']=verification
    ctx.write(Path(ctx.result)/'run-report.json',report)
    return report


def _read(path,default=None):return read(path) if Path(path).exists() else ({} if default is None else default)


def window_evidence_prefix(spec, *, schedule=QUALIFICATION):
    """Pure frozen research GCS phase layout; local paths remain recorded in matrix."""
    repeat=spec.get('repeat');phase=spec.get('phase');name=spec.get('id','')
    if repeat not in (1,2) or not name or Path(name).name!=name or name in ('.','..'):raise ValueError('invalid frozen window identity')
    if phase=='restart':
        if repeat!=(1 if schedule==REHEARSAL else 2):raise ValueError('restart window repetition does not match schedule')
        group='restarts'
    elif phase in ('smoke','baseline'):group=phase
    elif phase in ('resource','arbitration'):group='diagnostics/'+phase
    else:raise ValueError('unknown frozen window phase')
    return 'repeat-'+str(repeat)+'/'+group+'/'+name


def _window_token(ctx,spec):return 'w-'+hashlib.sha256((ctx.run_id+':'+spec['id']).encode()).hexdigest()[:18]


def _owned_delete(ctx,kind,name,namespace,owner,run_id,*,cluster=False,expected_uid=None):
    """Read ownership, then DELETE exact UID/resourceVersion via API precondition."""
    args=['get',kind,name]+([] if cluster else ['-n',namespace])
    response=ctx.kube(*args,'--ignore-not-found','-o','json',cleanup=True)
    if not response.stdout.strip():return
    document=json.loads(response.stdout);meta=document['metadata'];labels=meta.get('labels',{})
    if expected_uid is not None and meta['uid']!=expected_uid:
        raise ValueError('diagnostic cleanup UID changed: '+name)
    if labels.get('evaluation.karthos.dev/owner')!=owner or labels.get('evaluation.karthos.dev/run-id')!=run_id:
        raise ValueError('diagnostic cleanup ownership changed: '+name)
    plural, prefix = {
        'Pod':('pods','/api/v1'), 'DaemonSet':('daemonsets','/apis/apps/v1'),
        'Service':('services','/api/v1'), 'PersistentVolumeClaim':('persistentvolumeclaims','/api/v1'),
        'Namespace':('namespaces','/api/v1'), 'StorageClass':('storageclasses','/apis/storage.k8s.io/v1'),
        'MutatingWebhookConfiguration':('mutatingwebhookconfigurations','/apis/admissionregistration.k8s.io/v1'),
        'ValidatingWebhookConfiguration':('validatingwebhookconfigurations','/apis/admissionregistration.k8s.io/v1'),
        'ClusterRole':('clusterroles','/apis/rbac.authorization.k8s.io/v1'),
        'ClusterRoleBinding':('clusterrolebindings','/apis/rbac.authorization.k8s.io/v1'),
        'CustomResourceDefinition':('customresourcedefinitions','/apis/apiextensions.k8s.io/v1'),
    }[kind]
    uri=prefix+('' if cluster else '/namespaces/'+namespace)+'/'+plural+'/'+name
    options={'apiVersion':'v1','kind':'DeleteOptions','propagationPolicy':'Foreground',
             'preconditions':{'uid':meta['uid'],'resourceVersion':meta['resourceVersion']}}
    ctx.kube('delete','--raw',uri,'-f','-',input_text=json.dumps(options),cleanup=True)
    wait_seconds=180 if kind=='Namespace' else 120
    ctx.kube('wait','--for=delete',plural+'/'+name,*([] if cluster else ['-n',namespace]),
             '--timeout='+str(wait_seconds)+'s',timeout=wait_seconds+10,cleanup=True)


REFERENCE_POLICY={'period_seconds':1.,'max_gap_seconds':2.5,'boundary_slack_seconds':1.25,
                  'clock_gate_seconds':.25,'declared_before_measurement':True,
                  'coverage':'actual source acquisition brackets cover original generator window; no numeric tolerance fitting'}


def validate_reference_rows(rows,node,node_uid,pod_uid,run_id,start,end):
    from ..scoring import stamp
    errors=[];intervals=[];seen=set();previous=None;boots=set()
    try:start,end=stamp(start),stamp(end)
    except (TypeError,ValueError):return {'valid':False,'errors':['reference measurement bounds unavailable'],'policy':REFERENCE_POLICY}
    if end<=start:errors.append('reference measurement bounds non-positive')
    for row in rows:
        if row.get('node')!=node or row.get('node_uid')!=node_uid or row.get('observer_pod_uid')!=pod_uid or row.get('run_id')!=run_id or row.get('source')!='linux-proc' or row.get('error') or row.get('state')=='insufficient':
            errors.append('reference node/Pod UID/run/source identity or acquisition invalid')
        if not row.get('boot_id'):errors.append('reference boot identity unavailable')
        boots.add(row.get('boot_id'))
        try:
            a,b=float(row['acquisition_started_at']),float(row['acquisition_ended_at'])
            if not math.isfinite(a) or not math.isfinite(b) or b<a:raise ValueError('invalid bracket')
            identity=(row.get('metric'),json.dumps(row.get('dimensions'),sort_keys=True),a,b)
            if identity in seen:errors.append('duplicate reference measurement identity')
            seen.add(identity)
            pair=(a,b)
            if previous is not None and a<previous[0]:errors.append('reference acquisition timestamp regression')
            if pair!=previous:intervals.append(pair)
            previous=pair
        except (KeyError,TypeError,ValueError):errors.append('reference acquisition bracket unavailable or invalid')
    if len(boots)!=1:errors.append('reference boot lifecycle changed')
    slack=REFERENCE_POLICY['boundary_slack_seconds']
    relevant=[pair for pair in intervals if pair[1]>=start-slack and pair[0]<=end+slack]
    if not relevant or relevant[0][0]>start+slack or relevant[-1][1]<end-slack:errors.append('reference first/last acquisition does not cover original window')
    if any(b[0]-a[0]>REFERENCE_POLICY['max_gap_seconds'] for a,b in zip(relevant,relevant[1:])):errors.append('reference acquisition cadence gap')
    return {'valid':not errors,'errors':sorted(set(errors)),'records':len(rows),'acquisitions':len(intervals),
            'first_acquisition':intervals[0] if intervals else None,'last_acquisition':intervals[-1] if intervals else None,
            'measurement_bounds':[start,end],'policy':REFERENCE_POLICY}



class LiveDriver:
    def __init__(self,ctx):
        self.ctx=ctx;self.nodes=[];self.setup_count=0;self.setup_path=None;self.cell=None;self.paths={};self.last_spec=None

    def start_repeat(self,repeat):
        from . import provision
        from .setup import preflight
        provision.create_cluster(self.ctx,repeat,credentials=True)
        self.nodes=preflight(self.ctx)
        infra=[node for node in self.nodes if node['slot']=='infra']
        if len(infra)!=1:raise ValueError('exactly one observability generator node required')
        from .. import runner as r
        r.CONFIG['generator_node_name']=infra[0]['name']
        self.ctx.event('cluster-ready',repeat=repeat,nodes=self.nodes)

    def setup(self,spec,*,parent=None):
        from . import setup
        from .. import runner as r,pilot
        self.setup_count+=1;cell=Cell(spec['demo'],spec['source'],spec['repeat'])
        parent=(parent or self.ctx.result/('repetition-'+str(spec['repeat'])))/('setup-'+str(self.setup_count))
        if spec['phase']=='arbitration' and spec['scenario']=='replanned-trace':
            from .diagnostics.arbitration import transition_to_trace
            path=parent/cell.slug;path.mkdir(parents=True,exist_ok=False)
            before=self.ctx.json('get','configmap','karthos-source-contract','-n','karthos-system')
            self.ctx.write(path/'prior-source-contract.json',before)
            if json.loads(before['data']['source-plan.json']).get('metric_assignments',{}).get('servicegraph.request.count')!='mesh.istio':
                raise ValueError('trace replanning requires prior observed Istio source plan')
            def unavailable():
                r.reset_cell(False);r.prepare_source('trace');r.create_namespace(cell);r.install_demo(cell,path)
                pilot.install_observers(cell,path,include_agent=False,setup_no_surge=True);pilot.install_scripts(cell)
            def rediscover():
                profile=path/'transition-profile.json'
                self.ctx.command(['go','run','./cmd/discovery-job','--mode=discover','--kubeconfig='+str(self.ctx.kubeconfig),
                    '--cluster-name='+self.ctx.run_id,'--output='+str(profile),'--namespace=karthos-system','--target-namespace='+cell.namespace],cwd=r.REPO,timeout=600)
                return read(profile)
            def redeploy(profile):
                setup.deploy_karthos(self.ctx,cell,path)
                plan=read(path/'resolved-plan.json')
                if plan.get('metric_assignments',{}).get('servicegraph.request.count')!='traces.servicegraph':raise ValueError('rediscovery/redeployment did not select trace fallback')
                return plan
            transition=transition_to_trace(make_istio_unavailable=unavailable,rediscover=rediscover,redeploy=redeploy)
            self.ctx.write(path/'arbitration-transition.json',transition);self.setup_path=path
            from .setup import record_namespace_ownership
            record_namespace_ownership(self.ctx,cell,path)
        else:self.setup_path=setup.setup_cell(self.ctx,cell,parent)
        # Prepare exact query diagnostics before either readiness or measured
        # workload clocks. A cold Go cache must not consume a five-second poll.
        from .poll_capture import prewarm
        compiler_deadline=min(time.monotonic()+120, time.monotonic()+max(0,
            self.ctx.deadline-time.time()-self.ctx.config['limits']['cleanup_reserve_seconds']))
        prewarm(self.ctx,self.setup_path/'query-compiler-preparation.json',compiler_deadline)
        self.cell=cell;self.last_spec=spec
        from .provision import arm_watchdog
        arm_watchdog(self.ctx)

    def _apply_objects(self,objects):
        from .setup import apply
        for obj in objects:
            kind=obj['kind'];meta=obj['metadata'];args=['get',kind,meta['name']]+(['-n',meta['namespace']] if meta.get('namespace') else [])
            existing=self.ctx.kube(*args,'--ignore-not-found','-o','json',check=False)
            if existing.returncode or existing.stdout.strip():raise ValueError('diagnostic name already exists or cannot be checked: '+meta['name'])
        return self.ctx.kube('create','-f','-',input_text=json.dumps({'apiVersion':'v1','kind':'List','items':objects}))

    def _reference_start(self,namespace,token,path):
        from .diagnostics.resources import build_reference_daemonset
        image=self.ctx.config['readiness']['resource_helper_image']
        obj=build_reference_daemonset(namespace,image,token,nodes=self.nodes)
        obj['spec']['template'].setdefault('metadata',{}).setdefault('annotations',{})['sidecar.istio.io/inject']='false'
        obj['spec']['template']['spec']['containers'][0]['args']+=['--duration','3600']
        self.ctx.write(path/'reference-policy.json',REFERENCE_POLICY)
        self._pending_reference=obj
        self.ctx.write(path/'reference-daemonset.json',obj);self._apply_objects([obj])
        self.ctx.kube('rollout','status','daemonset/'+obj['metadata']['name'],'-n',namespace,'--timeout=180s',timeout=200)
        pods=self.ctx.json('get','pods','-n',namespace,'-l','evaluation.karthos.dev/component=host-reference,evaluation.karthos.dev/run-id='+token)['items']
        expected={node['name']:node['uid'] for node in self.nodes};actual={}
        for pod in pods:
            node=pod.get('spec',{}).get('nodeName')
            if node not in expected or node in actual or not pod.get('metadata',{}).get('uid'):raise ValueError('reference DaemonSet missing, duplicate, or unknown node identity')
            if not any(c.get('type')=='Ready' and c.get('status')=='True' for c in pod.get('status',{}).get('conditions',[])):raise ValueError('reference Pod not Ready')
            actual[node]=pod
        if set(actual)!=set(expected):raise ValueError('reference DaemonSet does not cover all three nodes')
        self.ctx.write(path/'reference-pods-before.json',{'items':pods})
        return obj,actual

    def _reference_stop(self,namespace,token,path,before):
        from .reference_spool import read_spool
        from ..structured_capture import pod_errors
        errors=[];coverage={}
        manifest=_read(path/'manifest.json')
        for node,pod in before.items():
            name=pod['metadata']['name']
            current=self.ctx.json('get','pod',name,'-n',namespace)
            lifecycle=pod_errors(pod,current)
            if lifecycle:
                errors.extend(node+': '+error for error in lifecycle)
                continue
            raw,status=read_spool(self.ctx,namespace,name)
            current=self.ctx.json('get','pod',name,'-n',namespace)
            status['pod_uid']=pod['metadata']['uid']
            status['node_name']=node
            status['lifecycle_errors']=pod_errors(pod,current)
            self.ctx.write(path/'reference-stream-status'/(node+'.json'),status)
            if raw is None or status['lifecycle_errors']:
                errors.append('reference Pod file or lifecycle invalid: '+node)
                continue
            records_path=path/'references'/(node+'.ndjson')
            records_path.parent.mkdir(parents=True,exist_ok=True)
            records_path.write_bytes(raw)
            rows=[json.loads(line) for line in raw.splitlines() if line.strip()]
            result=validate_reference_rows(rows,node,next(n['uid'] for n in self.nodes if n['name']==node),
                                           pod['metadata']['uid'],token,manifest.get('start'),manifest.get('end'))
            coverage[node]=result
            errors.extend(node+': '+error for error in result['errors'])
        result={'valid':not errors,'errors':sorted(set(errors)),'nodes':coverage,'policy':REFERENCE_POLICY};self.ctx.write(path/'reference-status.json',result)
        return result

    def _wait_measurement(self,path,stop):
        deadline=min(self.ctx.deadline-self.ctx.config['limits']['cleanup_reserve_seconds'],time.time()+1200)
        while not stop.is_set() and time.time()<deadline:
            try:manifest=_read(path/'manifest.json');job=_read(path/'job.json')
            except json.JSONDecodeError:
                stop.wait(.1);continue
            if manifest.get('phase')=='generator_and_export' and job:
                pods=self.ctx.json('get','pods','-n',self.cell.namespace,'-l','job-name='+job['metadata']['name']).get('items',[])
                active=[pod for pod in pods if pod.get('status',{}).get('phase')=='Running' and not pod.get('metadata',{}).get('deletionTimestamp')]
                if len(active)>1:raise ValueError('ambiguous generator Pod while scheduling diagnostic')
                if active:
                    pod=active[0];container=job['spec']['template']['spec']['containers'][0]['name']
                    response=self.ctx.kube('exec','-n',self.cell.namespace,pod['metadata']['name'],'-c',container,'--','tail','-c','131072','/tmp/eval-artifacts/generator.log',check=False,timeout=5)
                    if response.returncode==0:
                        from ..pilot import extract_events
                        windows=extract_events(response.stdout,'WINDOW')
                        if windows and windows[0].get('started_at'):
                            if pod.get('spec',{}).get('nodeName')!=next(node['name'] for node in self.nodes if node['slot']=='infra'):raise ValueError('generator scheduled on unclocked node')
                            self.ctx.write(path/'diagnostic-generator-start.json',{'pod':pod['metadata'],'node_name':pod['spec']['nodeName'],'marker':windows[0],'observed_at':time.time()})
                            return windows[0]
            stop.wait(1)
        raise RuntimeError('diagnostic requires observed generator start, unavailable before deadline/stop')

    def _stimulus(self,spec,path,token,stop,state,objects):
        try:
            marker=self._wait_measurement(path,stop)
            state.update(generator_start_marker=marker,triggered_at=time.time())
            if spec['phase']=='resource':
                from .diagnostics.resources import bind_network_target,build_resources_both,verify_network_result
                if self.ctx.manifest.get('schedule') in RECOVERABLE_SCHEDULES:
                    from .prepared_resources import release
                    state['release']=release(self.ctx,self,objects,path)
                    state['prepared_before_measurement']=True
                else:
                    objects.extend(build_resources_both(spec['scenario'],self.cell.namespace,self.nodes,self.ctx.config['readiness']['resource_helper_image'],token,duration_seconds=spec['duration_seconds']))
                    for obj in objects:
                        if obj['kind']=='StorageClass':obj['parameters']['labels']='evaluation-owner=gke-qualification-v1,evaluation-run='+self.ctx.run_id
                    self.ctx.write(path/'stimulus-objects.json',objects)
                    if spec['scenario']=='network':
                        server, client = objects
                        server_name = server['metadata']['name']
                        self._apply_objects([server])
                        self.ctx.kube('wait','--for=condition=Ready','pod/'+server_name,'-n',self.cell.namespace,'--timeout=120s',timeout=130)
                        admitted = self.ctx.json('get','pod',server_name,'-n',self.cell.namespace)
                        target = bind_network_target(server, client, admitted)
                        self.ctx.write(path/'stimulus-network-target.json',target)
                        self.ctx.write(path/'stimulus-network-client.json',client)
                        self._apply_objects([client])
                    else:self._apply_objects(objects)
                    for obj in objects:
                        if obj['kind']=='PersistentVolumeClaim':self.ctx.kube('wait','--for=jsonpath={.status.phase}=Bound','pvc/'+obj['metadata']['name'],'-n',self.cell.namespace,'--timeout=120s',timeout=130)
                    state['objects_created_at']=time.time()
                    if spec['scenario']=='storage':
                        # Storage creates provider disks. The pod-only stimuli are
                        # already covered by the armed cluster deadline.
                        from .provision import arm_watchdog
                        arm_watchdog(self.ctx)
                deadline=min(self.ctx.deadline-self.ctx.config['limits']['cleanup_reserve_seconds'],time.time()+spec['duration_seconds']+90)
                expected={obj['metadata']['name'] for obj in objects if obj['kind']=='Pod'}
                while time.time()<deadline:
                    pods=self.ctx.json('get','pods','-n',self.cell.namespace,'-l','evaluation.karthos.dev/run-id='+token).get('items',[])
                    selected=[pod for pod in pods if pod['metadata']['name'] in expected]
                    if self.ctx.manifest.get('schedule') in RECOVERABLE_SCHEDULES:
                        for pod in selected:
                            statuses=pod.get('status',{}).get('containerStatuses',[])
                            stimulus=next((c for c in statuses if c['name']=='stimulus'),{})
                            reference=next((c for c in statuses if c['name']=='volume-reference'),{})
                            if stimulus.get('state',{}).get('terminated') and reference.get('state',{}).get('running'):
                                self.ctx.kube('exec','-n',self.cell.namespace,pod['metadata']['name'],'-c','volume-reference','--',
                                    'python3','-c','import os,signal; os.kill(1,signal.SIGTERM)',timeout=10)
                    if len(selected)==len(expected) and all(pod.get('status',{}).get('phase') in ('Succeeded','Failed') for pod in selected):break
                    if stop.wait(1):raise RuntimeError('traffic window ended before stimulus completion observed')
                else:raise TimeoutError('resource helpers did not complete within bounded duration')
                self.ctx.write(path/'stimulus-pods-after.json',{'items':selected})
                state['helper_lifecycles']=[{'pod':pod['metadata']['name'],'uid':pod['metadata'].get('uid'),'node_name':pod.get('spec',{}).get('nodeName'),'start_time':pod['status'].get('startTime'),'containers':pod['status'].get('containerStatuses',[])} for pod in selected]
                for pod in selected:
                    for container in pod['spec']['containers']:
                        text=self.ctx.kube('logs',pod['metadata']['name'],'-n',self.cell.namespace,'-c',container['name'],timeout=60).stdout
                        filename=(path/'references'/'volumes'/(pod['metadata']['name']+'.ndjson')) if container['name']=='volume-reference' else path/'stimulus-logs'/(pod['metadata']['name']+'__'+container['name']+'.log')
                        self.ctx.write(filename,text)
                if spec['scenario']=='network':
                    for pod in selected:
                        verify_network_result((path/'stimulus-logs'/(pod['metadata']['name']+'__stimulus.log')).read_text())
                if any(pod['status']['phase']!='Succeeded' for pod in selected):raise RuntimeError('resource helper failed; logs retained')
            elif spec['phase']=='restart':
                from .diagnostics.restarts import observe_restart, restart_target
                target=spec['scenario'];namespace='karthos-system' if target=='gateway' else self.cell.namespace
                local=SimpleNamespace(kube=self.ctx.kube,write=self.ctx.write,evidence_dir=path/'restart',clock=time.time)
                target_name='karthos-gateway' if target=='gateway' else 'checkoutservice'
                deployment=self.ctx.json('get','deployment',target_name,'-n',namespace)
                selector=','.join(key+'='+value for key,value in sorted(deployment['spec']['selector']['matchLabels'].items()))
                state['pods_before']=self.ctx.json('get','pods','-n',namespace,'-l',selector)
                self.ctx.write(path/'restart/pods-before.json',state['pods_before'])
                capture_plan = read(path/'structured/gateway/snapshot.json') if target == 'gateway' else None
                state['request']=restart_target(local,namespace,target,self.ctx.run_id,capture_plan=capture_plan)
                budget = min(300., self.ctx.deadline - self.ctx.config['limits']['cleanup_reserve_seconds'] - time.time())
                state['after'], state['pods_after'] = observe_restart(
                    self.ctx.kube, namespace, state['request']['name'], selector, self.ctx.run_id,
                    deployment, state['pods_before'], timeout=budget)
                self.ctx.write(path/'restart/pods-after.json',state['pods_after'])
            state.update(state='complete',finished_at=time.time())
        except Exception as exc:state.update(state='incomplete',error=str(exc),finished_at=time.time())
        self.ctx.write(path/'scenario-result.json',state)

    def _selection(self,path,spec):
        from .diagnostics.arbitration import assert_selection
        from ..structured_capture import load_config
        contract=self.ctx.json('get','configmap','karthos-source-contract','-n','karthos-system')
        mounted_profile=json.loads(contract['data']['capability-profile.json']);mounted_plan=json.loads(contract['data']['source-plan.json'])
        profile=read(self.setup_path/'profile.json');plan=read(self.setup_path/'resolved-plan.json')
        configs=[]
        for name in ('karthos-agent-config','karthos-gateway-config'):
            cm=self.ctx.json('get','configmap',name,'-n','karthos-system')
            keys=[key for key in cm.get('data',{}) if key.endswith(('.yaml','.yml'))]
            if len(keys)!=1:raise ValueError('collector configuration unavailable or ambiguous')
            configs.append(load_config(cm['data'][keys[0]]));self.ctx.write(path/'selection'/(name+'.json'),cm)
        samples=[]
        for name in ('servicegraph.request.count','servicegraph.request.error.count','servicegraph.request.duration'):
            for sample in _read(path/'extended/api'/(name+'.json')).get('data',{}).get('samples',[]):samples.append(dict(sample,metric_name=name))
        result=assert_selection(profile,{'source_plan':plan,'backend_source_contract':{'capabilityProfile':mounted_profile,'sourcePlan':mounted_plan},'collector_configs':configs},samples,source_id(self.cell.source))
        if spec.get('scenario')=='both-sources':
            available={cap.get('source_id') for cap in profile.get('capabilities',[]) if cap.get('status')=='available'}
            if not {'mesh.istio','traces.servicegraph'}<=available:result={'state':'insufficient','reason':'both-sources scenario did not observe both available source capabilities','selection':result}
        self.ctx.write(path/'selection/profile.json',profile);self.ctx.write(path/'selection/plan.json',plan);self.ctx.write(path/'source-selection.json',result)
        return result

    def window(self,spec):
        from .. import timing_pilot,runner as r
        from .evaluate import capture_extended,evaluate
        from .consumer import capture_consumer
        parent=self.ctx.result/('repetition-'+str(spec['repeat']))/'cells'/self.cell.slug
        parent.mkdir(parents=True,exist_ok=True)
        timing_spec=dict(name=spec['id'],kind='confirmation',generator=spec['generator'],duration_seconds=spec['duration_seconds'],rate=spec['rate'],workload='checkout-heavy',expected_nodes=3)
        path=parent/'windows'/spec['id'];self.paths[spec['id']]=path
        token=_window_token(self.ctx,spec);stop=threading.Event();state={'state':'pending','scenario':spec['scenario'],'started_at':time.time()};objects=[];thread=None;reference_obj=None;reference_pods={};traffic=None;timing_error=None;cleanup_errors=[]
        if spec['phase'] == 'restart' and spec['scenario'] == 'gateway':
            timing_spec['gateway_restart_token'] = token
        # timing_pilot exclusively reserves the window directory. Sidecars wait
        # for that reservation before persisting any evidence into it.
        scratch=self.ctx.result/('repetition-'+str(spec['repeat']))/'reference-setup'/spec['id'];scratch.mkdir(parents=True,exist_ok=False)
        from .setup import verify_versions
        self.ctx.write(scratch/'versions-before.json',verify_versions(self.ctx))
        try:
            reference_obj,reference_pods=self._reference_start(self.cell.namespace,token,scratch)
            if spec['phase']=='resource' and self.ctx.manifest.get('schedule') in RECOVERABLE_SCHEDULES:
                from .prepared_resources import prepare
                prepare(self.ctx,self,spec,token,scratch,objects)
            if spec['phase'] in ('resource','restart'):
                thread=threading.Thread(target=self._stimulus,args=(spec,path,token,stop,state,objects),name='stimulus-'+token,daemon=True);thread.start()
            try:traffic=timing_pilot.run_window(self.cell,parent,timing_spec,self.ctx.run_id+':'+spec['id'],
                                                measure=lambda phase: timed(self.ctx,phase,spec['id']))
            except Exception as exc:
                timing_error=str(exc)
                self.ctx.write(path/'timing-failure.json',{'error_type':type(exc).__name__,'message':timing_error,'traceback':traceback.format_exc(),'window':spec['id']})
                traffic=_read(path/'timing-verification.json',{'independent_valid':False,'state':'incomplete','reason':timing_error})
            if not path.exists():path.mkdir(parents=True,exist_ok=False)
            if timing_error:stop.set()
            if thread:
                thread.join(timeout=spec['duration_seconds']+150)
                if thread.is_alive():raise RuntimeError('diagnostic worker still running; refuses cleanup/next window')
            manifest=_read(path/'manifest.json');start=manifest.get('start');end=manifest.get('end')
            with timed(self.ctx,'evidence-reads',spec['id']):
                if start is not None and end is not None:capture_extended(self.ctx,self.cell,path,start,end)
                self._reference_stop(self.cell.namespace,token,path,reference_pods)
            for file in scratch.rglob('*'):
                if file.is_file():
                    target=path/'reference-setup'/file.relative_to(scratch);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(file,target)
            if self.setup_path:
                for file in self.setup_path.glob('*.json'):shutil.copy2(file,path/('setup-'+file.name))
            if start is not None and end is not None:
                if self.ctx.manifest.get('schedule') in THESIS_SCHEDULES:
                    from .thesis import replica_inventory
                    service='checkoutservice' if spec['demo']=='boutique' else 'checkout'
                    try:before_replicas=replica_inventory(self.ctx,self.cell.namespace,service)
                    except (ValueError,RuntimeError,OSError,KeyError):before_replicas=None
                    consumer=capture_consumer(self.ctx,path,spec['phase'],spec=spec)
                    try:after_replicas=replica_inventory(self.ctx,self.cell.namespace,service)
                    except (ValueError,RuntimeError,OSError,KeyError):after_replicas=None
                    self.ctx.write(path/'consumer/kubernetes-oracle.json',{'before':before_replicas,'after':after_replicas})
                else:
                    consumer=capture_consumer(self.ctx,path,spec['phase'])
                self.ctx.write(path/'consumer-status.json',consumer)
                selection=self._selection(path,spec)
            else:consumer={'state':'incomplete','reason':'generator bounds unavailable'};selection={'state':'insufficient','reason':'public sample bounds unavailable'}
            with timed(self.ctx,'scoring',spec['id']):
                report=evaluate(self.ctx,spec,path,traffic_report=traffic)
            report['path']=str(path);report['consumer']=consumer;report['source_selection']=selection;report['timing_error']=timing_error
            if self.ctx.manifest.get('schedule') in THESIS_SCHEDULES:
                from .thesis import score_window_answers
                reference_inventory=_read(path/'consumer/kubernetes-oracle.json')
                report['thesis_answers']=score_window_answers(_read(path/'consumer/ledger.json'),
                    reference_inventory.get('before'),reference_inventory.get('after'),report)
            reference=_read(path/'reference-status.json')
            consumer_ok=consumer.get('state') in ('complete','observed','available') and consumer.get('valid',True) is True
            report['diagnostic_required_evidence']={'valid':bool(reference.get('valid') and consumer_ok and selection.get('state')=='pass'),'reference':reference.get('valid') is True,'consumer':consumer_ok,'selection':selection.get('state')=='pass'}
            if not reference.get('valid') or not consumer_ok or selection.get('state')!='pass':
                report['evidence'].update(state='incomplete',valid=False)
                report['reason']='reference, API-only consumer, or deployed source-selection evidence incomplete'
            if spec['phase'] in ('resource','restart'):
                report['execution']['state']='complete' if state.get('state')=='complete' and _read(path/'manifest.json').get('generator_artifacts_complete') is True else 'incomplete'
            if timing_error and spec['phase'] not in ('resource','restart'):
                report['execution']['state']='incomplete';report['reason']=timing_error
            self.ctx.write(path/'versions-after.json',verify_versions(self.ctx))
            self.ctx.write(path/'gke-evaluation.json',report)
            return report
        finally:
            stop.set()
            if thread and thread.is_alive():thread.join(timeout=10)
            if thread and thread.is_alive():raise RuntimeError('stimulus worker remains alive; cluster-level cleanup required')
            if path.exists():
                for file in scratch.rglob('*'):
                    if file.is_file():
                        target=path/'reference-setup'/file.relative_to(scratch);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(file,target)
            reference_obj=reference_obj or getattr(self,'_pending_reference',None)
            for obj in reversed(objects+([reference_obj] if reference_obj else [])):
                try:_owned_delete(self.ctx,obj['kind'],obj['metadata']['name'],obj['metadata'].get('namespace'), 'gke-qualification-v1',token,cluster=obj['kind']=='StorageClass',expected_uid=obj['metadata'].get('uid'))
                except Exception as exc:cleanup_errors.append(str(exc))
            if path.exists():self.ctx.write(path/'diagnostic-cleanup.json',{'valid':not cleanup_errors,'errors':cleanup_errors})
            self._pending_reference=None
            if cleanup_errors:
                saved=_read(path/'gke-evaluation.json')
                if saved:
                    saved.setdefault('evidence',{}).update(state='incomplete',valid=False,diagnostic_cleanup_errors=cleanup_errors)
                    self.ctx.write(path/'gke-evaluation.json',saved)
                raise RuntimeError('ownership-safe diagnostic cleanup incomplete: '+str(cleanup_errors))

    def upload(self,spec,report,*,evidence_prefix=None):
        path=self.paths.get(spec['id'])
        if path is None or not path.exists():
            path=self.ctx.result/'failed-windows'/spec['id'];path.mkdir(parents=True,exist_ok=False);self.ctx.write(path/'failure.json',report)
            scratch=self.ctx.result/('repetition-'+str(spec['repeat']))/'reference-setup'/spec['id']
            if scratch.exists():shutil.copytree(scratch,path/'reference-setup')
        trace=self.ctx.result/'failure-traces'/(spec['id']+'.json')
        if trace.is_file():shutil.copy2(trace,path/'unhandled-failure.json')
        bucket=self.ctx.state['terraform']['foundation']['evidence_bucket']
        local_seal=exports.seal(path)
        if not local_seal['valid']:raise ValueError('window local evidence seal invalid')
        archives=self.ctx.result/'window-archives'/spec['id'];archives.mkdir(parents=True,exist_ok=False)
        original_bytes=sum(file.stat().st_size for file in exports.evidence_files(path))
        maximum=self.ctx.config['limits'].get('max_evidence_bytes',100*1024**3)
        if original_bytes+self.ctx.state.get('cumulative_raw_evidence_bytes',0)>maximum:raise ValueError('total evidence budget exceeded before archive')
        if shutil.disk_usage(archives).free<original_bytes+512*1024**2:raise ValueError('insufficient archive headroom')
        with tempfile.TemporaryDirectory(prefix='gke-window-projection-') as temporary:
            projection=Path(temporary).resolve()/'evidence'
            with timed(self.ctx,'privacy-projection',spec['id']) as info:
                info['input_bytes']=original_bytes
                from .quarantine import from_context
                projected=exports.export_projection(path,projection,quarantine=from_context(self.ctx))
            if not projected['valid']:raise ValueError('window redacted projection invalid')
            shutil.copy2(path/'GKE-SHA256SUMS',projection/'LOCAL-ORIGINAL-GKE-SHA256SUMS')
            exports.seal(projection)
            with timed(self.ctx,'archive-packing',spec['id']) as info:
                parts = exports.pack_window_archives(projection, archives)
                info['archive_bytes']=sum(part['bytes'] for part in parts)
        prefix = evidence_prefix or window_evidence_prefix(spec,schedule=self.ctx.manifest.get('schedule',QUALIFICATION))
        receipt=exports.upload(self.ctx,archives,bucket=bucket,prefix='gke-qualification-v1/runs/'+self.ctx.run_id+'/'+prefix)
        archive_bytes = sum(part['bytes'] for part in parts)
        receipt.update(archive_index='window-index.json', archive_parts=len(parts), archive_bytes=archive_bytes,
                       original_window_seal_sha256=sha(path/'GKE-SHA256SUMS'),redacted_projection=True)
        if len(parts) == 1:
            receipt.update(archive=parts[0]['name'], archive_sha256=parts[0]['sha256'])
        self.ctx.state['cumulative_raw_evidence_bytes']=self.ctx.state.get('cumulative_raw_evidence_bytes',0)+original_bytes
        self.ctx.state['uploaded_evidence_bytes']=self.ctx.state.get('uploaded_evidence_bytes',0)+archive_bytes
        return receipt

    def failure_hold(self,spec,report):
        """Bounded read-only recovery interval; the absolute deadline is unchanged."""
        holds=getattr(self,'_holds',{})
        if spec['id'] in holds:return holds[spec['id']]
        until=min(time.time()+self.ctx.config['limits']['diagnostic_hold_seconds'],self.ctx.deadline-self.ctx.config['limits']['cleanup_reserve_seconds'])
        status={'state':'holding','window':spec['id'],'started_at':time.time(),'until':until,
                'absolute_infrastructure_deadline':self.ctx.deadline,'new_traffic_permitted':False,
                'reason':report.get('reason','required recorder/export evidence unavailable'),
                'recovery':'inspect retained artifacts and workload containers; no retries, ACK or replacement workload authorized by this hold'}
        holds[spec['id']]=status;self._holds=holds
        target=self.ctx.result/'diagnostic-holds'/(spec['id']+'.json');self.ctx.write(target,status)
        self.ctx.event('diagnostic-hold',window=spec['id'],until=until,new_traffic_permitted=False)
        while time.time()<until:
            time.sleep(min(5.,max(0.,until-time.time())))
        status.update(state='expired',finished_at=time.time());self.ctx.write(target,status)
        return status

    def checkpoint(self,matrix,records):
        sequence=len(records);directory=self.ctx.result/'checkpoints'/('checkpoint-'+str(sequence).zfill(3));directory.mkdir(parents=True,exist_ok=False)
        exports.render_matrix(directory,matrix,records)
        self.ctx.write(directory/'reviewed-manifest.json',self.ctx.manifest)
        self.ctx.write(directory/'progress.json',{'executed_windows':sequence,'window':records[-1]['id'],'records':records,'observed_at':time.time()})
        if (self.ctx.result/'progress.ndjson').exists():shutil.copy2(self.ctx.result/'progress.ndjson',directory/'progress.ndjson')
        return exports.upload(self.ctx,directory,bucket=self.ctx.state['terraform']['foundation']['evidence_bucket'],prefix='gke-qualification-v1/runs/'+self.ctx.run_id+'/checkpoints/'+directory.name)

    def thesis_history(self,spec):
        from .thesis_history import capture
        return capture(self.ctx,self.cell,spec)

    def finish(self):
        from .teardown import destroy_cluster
        result=destroy_cluster(self.ctx)
        return {'cluster_cleanup':result,'controller_retained':True,'final_operator_teardown_required':True}


def run(ctx):
    """Actual approved controller command; never invoked by offline tests."""
    from .setup import legacy_context,stage
    from .readiness import validate_launch
    validate_launch(ctx)
    report_path=ctx.result/'run-report.json'
    if report_path.exists():raise ValueError('run evidence already exists; inspect retained execution instead of rerunning')
    from .readiness_live import ProbeDriver, run as readiness_gate
    driver=LiveDriver(ctx)
    try:
        if ctx.config.get('execution_scope') == 'readiness-only':
            import unittest
            suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in (
                'evaluation.tests.test_gke_readiness_only.ReadinessOnlyTests',
                'evaluation.tests.test_gke_readiness_capture_failure.ReadinessFailureEvidenceTests'))
            checked=unittest.TextTestRunner(verbosity=2).run(suite)
            ctx.write(ctx.result/'readiness-scope-contract.json',{'tests':checked.testsRun,'passed':checked.wasSuccessful(),'executed_on':'GCP controller'})
            if not checked.wasSuccessful():raise RuntimeError('readiness-only controller contract checks failed')
        with timed(ctx,'controller-stage'):
            stage(ctx)
    except (Exception,KeyboardInterrupt) as exc:
        from .readiness import execute
        ctx.write(ctx.result/'failure-traces/controller-stage.json', {
            'error_type':type(exc).__name__, 'message':str(exc), 'traceback':traceback.format_exc()})
        reason='controller staging failed: '+(str(exc) or type(exc).__name__)
        def failed_stage(context, active):
            probes=ProbeDriver(active)
            return execute(context,None,None,probes.publish,initial_failure=reason)
        if ctx.manifest.get('schedule') in RECOVERABLE_SCHEDULES:
            from .recoverable_run import failed_staging
            report=failed_staging(ctx,driver,reason)
        else:report=execute_matrix(ctx,driver,gate=failed_stage)
    else:
        with legacy_context(ctx):
            report=execute_matrix(ctx,driver,gate=None if ctx.manifest.get('schedule') in RECOVERABLE_SCHEDULES else readiness_gate)

    summary=ctx.result/'final-summary';summary.mkdir(exist_ok=False)
    for name in ('matrix.json','matrix.csv','matrix.html','attempts.json','attempts.csv','run-report.json','claim-matrix.json'):
        if (ctx.result/name).is_file():shutil.copy2(ctx.result/name,summary/name)
    # Deployment failures can precede the first window. Export their diagnostics
    # before independent cleanup removes the controller's local files.
    infrastructure=ctx.result/'infrastructure'
    if infrastructure.is_dir():shutil.copytree(infrastructure,summary/'infrastructure')
    readiness_matrix=ctx.result/'readiness/matrix/matrix.json'
    if readiness_matrix.is_file():
        shutil.copy2(readiness_matrix,summary/'readiness-matrix.json')
    if ctx.manifest.get('schedule') in RECOVERABLE_SCHEDULES:
        for folder in ('checkpoint-receipts','readiness'):
            source=ctx.result/folder
            if source.is_dir():shutil.copytree(source,summary/folder)
    if ctx.manifest.get('schedule') in RECOVERABLE_SCHEDULES:
        from .failure_evidence import retain_attempt_reports, copy_summary_diagnostics
        records=report.get('records',report.get('windows',[]))
        retain_attempt_reports(ctx,records,summary)
        copy_summary_diagnostics(ctx,records,summary)
    traces=ctx.result/'failure-traces'
    if traces.is_dir():shutil.copytree(traces,summary/'failure-traces')
    for name in ('readiness-scope-contract.json','controller-headroom.json','node-inventory.json','kubernetes-version.json','progress.ndjson','phase-timings.ndjson'):
        if (ctx.result/name).is_file():shutil.copy2(ctx.result/name,summary/name)

    receipt=exports.upload(ctx,summary,bucket=ctx.state['terraform']['foundation']['evidence_bucket'],prefix='gke-qualification-v1/runs/'+ctx.run_id+'/final-summary')
    ctx.write(ctx.result/'final-summary-export.json',receipt)
    from .provision import request_final_cleanup
    try:
        cleanup_request=request_final_cleanup(ctx,receipt)
        if cleanup_request.get('cleanup_requested') is not True:raise RuntimeError('external final cleanup activation not verified')
    except Exception as exc:
        cleanup_request={'state':'cleanup-request-unverified','cleanup_requested':False,'completion_confirmed':False,'error':str(exc),'independent_deadline_remains_required':True}
    ctx.write(ctx.result/'final-cleanup-request.json',cleanup_request)
    result=dict(report,final_cleanup_request=cleanup_request,final_summary_export=receipt)
    if cleanup_request.get('cleanup_requested') is not True:result['state']='incomplete'
    elif result['state']=='complete':result['state']='awaiting-cleanup-confirmation'
    ctx.write(ctx.result/'runtime-result.json',result)
    ctx.event('execution-finished',state=result['state'],summary_export=receipt,controller_retained=not cleanup_request.get('completion_confirmed',False))
    return result
