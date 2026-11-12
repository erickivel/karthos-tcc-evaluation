"""Four-cell diagnostic timing study. Never rewrites historical results."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import os
import shutil
import signal
import time
from datetime import datetime,timedelta,timezone
from pathlib import Path
from . import runner as r,pilot
from .protocol import cells_for_repeat,source_id

PROTOCOL='timing-1.4'
LIVE_SECONDS=4*3600
OFFSETS=[20,29,31,44,46,59,61,74,76,89,91,100]
DEFAULT_BASELINE='repairs-v4-20261023-d'

def plan():
 rows=[]
 for index,cell in enumerate(cells_for_repeat(1)):
  generators=('k6','locust') if index%2==0 else ('locust','k6')
  for kind,duration in (('probe',120),('confirmation',300)):
   for generator in generators:
    rows.append(dict(cell=cell.slug,demo=cell.demo,source=cell.source,generator=generator,kind=kind,
      name=generator+'-'+kind,duration_seconds=duration,rate=2,workload='checkout-heavy'))
 return rows

def safe_run_id(value):
 if not value or Path(value).name!=value or value in ('.','..') or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in value):
  raise ValueError('run id must be one bounded directory name')
 return value

def reserve_budget(duration,remaining):
 # Workload, setup, normal export/quiet/drain and an end-of-study cleanup reserve.
 required=duration+180+60+90+180+180+180+300
 if remaining<required:raise TimeoutError('insufficient four-hour budget for capture/workload/export/restoration')

def make_job(cell,spec,window_id):
 from .manifests import k6_job,matched_locust_job
 generator=spec['generator']
 if generator not in ('k6','locust'):raise ValueError('unsupported load generator: '+str(generator))
 builder=k6_job if generator=='k6' else matched_locust_job
 image=r.CONFIG['k6_image'] if generator=='k6' else r.CONFIG[cell.demo+'_locust_image']
 job=builder(cell.demo,cell.source,spec['name'],cell.namespace,image,
  'http://frontend:80' if cell.demo=='boutique' else 'http://frontend-proxy:8080',
  duration_seconds=spec['duration_seconds'],rate=2,workload='checkout-heavy',artifact_mode=True,
  recovery_seconds=r.CONFIG['artifact_recovery_seconds'])
 container=job['spec']['template']['spec']['containers'][0]
 if spec['kind']=='probe':
  if generator=='k6':container['args'][0]=container['args'][0].replace('/scripts/run.js','/scripts/timing_probe.js')
  else:container['args'][0]=container['args'][0].replace('/scripts/matched_locust.py','/scripts/timing_probe.py').replace('--users 10 --spawn-rate 10','--users 1 --spawn-rate 1').replace('--run-time 120s ','')
 container['env']=[x for x in container['env'] if x['name'] not in ('RUN_ID','EVAL_CORRELATE_TRACES')]+[
  {'name':'RUN_ID','value':window_id},{'name':'EVAL_CORRELATE_TRACES','value':'1'}]
 job['spec']['activeDeadlineSeconds']=spec['duration_seconds']+180+r.CONFIG['artifact_export_seconds']+r.CONFIG['artifact_recovery_seconds']
 return job

def load(path):return json.loads(Path(path).read_text())

def digest(path):
 with Path(path).open('rb') as source:return hashlib.file_digest(source,'sha256').hexdigest()

def preparation(baseline,root):
 """Offline qualification, before starting any live deadline or cluster work."""
 from .repair_pilot import readiness_issues
 previous=load(baseline/'continuation-report.json')
 if previous.get('state')!='complete':raise RuntimeError('baseline campaign is not complete')
 # Product qualification and the exact baseline input pins remain authoritative.
 readiness=r.OUT/'repairs-v4-preparation-20261021/readiness-v4-final-20261022.json'
 issues=readiness_issues(load(readiness),readiness.parent)
 if issues:raise RuntimeError('historical preparation evidence changed: '+str(issues))
 pins=previous['pins']
 if r.repository_fingerprint(r.REPO)!=pins['karthos_source_hash']:
  raise RuntimeError('product source differs from validated immutable baseline')
 findings=r.ROOT/'analyses/protocol4-final-findings-20261023/analysis.json'
 report=load(findings);refs=report['evidence_references']
 for item in refs:
  if digest(item['path'])!=item['sha256']:raise RuntimeError('changed preserved evidence: '+item['path'])
 # Reanalyse both timestamp populations into this new preparation record.
 from .timing_analysis import analyze_window
 combined=load(r.ROOT/'analyses/protocol4-sampler-resumed-20261023/report.json')
 retrospective=[]
 for row in combined['window_results']:
  selected=row.get('selected')
  if not selected or selected.get('state')!='incomplete':continue
  new=analyze_window(Path(selected['path']))
  retrospective.append(dict(path=selected['path'],original_state=selected['state'],new_timing_analysis=new))
 r.write(root/'preserved-timing-reanalysis.json',dict(original_scores_unchanged=True,windows=retrospective,
   interpretation='Historical clock uncertainty is unavailable; do not claim subsecond timing attribution. All six known source/observer counts have equal start/completion totals.'))
 hypotheses=[
  dict(id='event-membership',prediction='Starts and completions crossing a boundary give different event populations; report both.'),
  dict(id='emission-availability',prediction='A completed event can precede source emission and stored/API availability; distinguish timestamps from observation bounds.'),
  dict(id='query-boundary',prediction='Product increase/window queries may differ from bracketing and settled deltas while exact API/storage parity holds.'),
  dict(id='generator-pacing',prediction='The confirmation workload burst shape can differ between tools; scheduled probes separate this from accounting.'),
  dict(id='duration-population',prediction='Equal duration values do not establish population equivalence; matched sums/counts are required.'),
  dict(id='agent-dependency',prediction='The retained prompt trace links HTTP301 to APIStatusError and HTTP500; recurrence and original dependency selection remain separate.')]
 result=dict(qualified=True,baseline=str(baseline),findings_sha256=digest(findings),checked_references=len(refs),
  preserved_incomplete_windows=report['incomplete_windows'],hypotheses=hypotheses,
  frozen_semantics='No source intervals/query policy/product changes; all old verdicts retained.')
 r.write(root/'preparation.json',result)
 return pins

def export_intervals(cell,path,manifest):
 """Preserve product-rendered queries independently for every declared interval."""
 from .semantics_pilot import export_parity
 from .verify import parse_time
 intervals={'whole':(manifest['start'],manifest['end'])}
 if manifest['kind']=='probe':
  begin=parse_time(manifest['start'])
  intervals['inner']=((begin+timedelta(seconds=30)).isoformat(),(begin+timedelta(seconds=90)).isoformat())
 for name,(start,end) in intervals.items():
  dest=path/'intervals'/name;dest.mkdir(parents=True,exist_ok=False)
  seconds=round((parse_time(end)-parse_time(start)).total_seconds())
  pilot.export_window(cell,dest,start,end,seconds)
  export_parity(cell,dest,start,end,seconds)
  r.write(dest/'interval.json',dict(start=start,end=end,bounds='[start,end)',query_seconds=seconds))
  # Exact query verification uses the same compiler and both directions of series matching.
  from .scoring import exact_query_parity
  checks={key:exact_query_parity(dest,filename,source_id(cell.source)) for key,filename in (
    ('exact_request_count_api','api-request-count.json'),('exact_error_count_api','api-error-count.json'),('exact_duration_api','api-duration.json'))}
  r.write(dest/'parity.json',checks)
 return intervals

def stop_captures(availability,capture):
 result={}
 worker_stopped=True
 if availability:
  try:
   result['availability']=availability.stop()
   worker_stopped=result['availability'].get('worker_stopped') is True
  except Exception as exc:
   result['availability']={'valid':False,'error':str(exc)};worker_stopped=False
 if capture:
  if worker_stopped:
   try:result['structured_capture']=capture.stop()
   except Exception as exc:result['structured_capture']={'valid':False,'error':str(exc)}
  else:
   # Refuse restoration while the reader is active. Stop other log samplers;
   # the independently owned watchdog retains guarded restoration authority.
   try:
    if getattr(capture,'capture',None):capture.capture.stop()
   except Exception as exc:result['sampler_stop_error']=str(exc)
   result['structured_capture']={'valid':False,'watchdog_retained':True,
      'error':'availability worker not confirmed stopped; restoration deferred to ownership-checked watchdog'}
 return result

def run_window(cell,parent,spec,study,*,measure=None):
 from contextlib import nullcontext
 measure = measure or (lambda phase: nullcontext())
 from .structured_capture import RepairCapture
 from .timing_capture import AvailabilityCapture,clock_samples
 from .generator_execution import execute_generator
 from .semantics_pilot import quiet_counter
 from .repair_pilot import prime_after_instrumentation,window_bounds
 from .timing_analysis import analyze_window
 path=parent/'windows'/spec['name'];path.mkdir(parents=True,exist_ok=False)
 reserve_budget(spec['duration_seconds'],r._DEADLINE-time.monotonic())
 window_id=':'.join((study,cell.slug,spec['kind'],spec['generator']))
 job=make_job(cell,spec,window_id);mode='timing-probe' if spec['kind']=='probe' else 'matched'
 manifest={**spec,'protocol_version':5,'timing_protocol':PROTOCOL,'artifact_protocol':1,
  'run_id':window_id,'study_run_id':study,'namespace':cell.namespace,'source_id':source_id(cell.source),
  'mode':mode,'phase':'capture_setup','controller_start':r.utc(),
  'probe_expected_checkout_operations':24 if spec['kind']=='probe' else None,
  'probe_offsets_seconds':OFFSETS if spec['kind']=='probe' else None}
 r.write(path/'manifest.json',manifest);r.write(path/'job.json',job)
 capture=availability=None;verified=False;error=None
 try:
  settings=dict(r.CONFIG,capture_expected_seconds=spec['duration_seconds']+2490,
                gateway_restart_token=spec.get('gateway_restart_token'))
  capture=RepairCapture(cell,path,time.time()+max(0,r._DEADLINE-time.monotonic()),config=settings)
  capture.start()
  if settings.get('expected_nodes',1)>1:
   from .gke.run import capture_clocks
   clock=capture_clocks(capture,r.CONFIG.get('generator_node_name'))
  else:clock=clock_samples(capture,node_name=capture.pods['agent']['spec']['nodeName'])
  r.write(path/'clock.json',clock)
  manifest['clock_uncertainty_seconds']=clock.get('clock_uncertainty_seconds');manifest['clock_evidence']=clock
  if not clock.get('valid'):raise RuntimeError('250ms timestamp uncertainty gate failed: '+str(clock))
  availability=AvailabilityCapture(cell,path,capture)
  availability.start()
  manifest['counter_priming']=prime_after_instrumentation(cell,path,window_id+':prime',parent.parent.parent)
  manifest['phase']='preroll';r.write(path/'manifest.json',manifest)
  pilot.sleep_bounded(r.CONFIG['capture_preroll_seconds'])
  before=quiet_counter(cell,path,'before')
  if not before.get('stable'):raise RuntimeError('fresh pre-load counter stability unavailable')
  # Retain measured cadence as calibration, not an assertion of phase locking.
  r.write(path/'calibration.json',dict(measured_interval_seconds=before['measured_collection_interval_seconds'],
    samples=before['records'],probe_origin='generator T0; emission phase verified after capture'))
  pilot.configure_generator_deadline(job,spec['duration_seconds'],r.CONFIG,r._DEADLINE-time.monotonic(),time.time())
  manifest['phase']='generator_and_export';r.write(path/'manifest.json',manifest)
  with measure('load-and-generator-export'):
   result=execute_generator(job,cell.namespace,path,window_id,spec['generator'],mode,export_timeout_seconds=r.CONFIG['artifact_export_seconds'])
  verified=pilot.generator_log_complete(result)
  manifest.update(generator_artifacts_complete=verified,generator_job_failed=result['workload_exit_code']!=0,
    workload_exit_code=result['workload_exit_code'],generator_log_pod=result.get('pod_name'))
  pilot.lock_runtime_images(parent.parent.parent,r.image_ids(load(path/'generator-pods.json')))
  records=load(path/'generator-window.json');ledger=[json.loads(line) for line in (path/'ledger.ndjson').read_text().splitlines() if line.strip()]
  manifest.update(window_bounds(records[0]['started_at'],records[-1]['ended_at'],spec['duration_seconds'],ledger))
  manifest['phase']='drain';r.write(path/'manifest.json',manifest)
  with measure('drain'):
   pilot.sleep_bounded(r.CONFIG['telemetry_drain_seconds'])
   after=quiet_counter(cell,path,'after')
  if not after.get('stable'):raise RuntimeError('fresh post-load counter stability unavailable')
 except BaseException as exc:
  error=exc;manifest.update(phase='incomplete',interrupted=True,error=str(exc))
 finally:
  # Availability must stop before instrumentation restoration removes files.
  with measure('capture-archive-and-restore'):
   manifest.update(stop_captures(availability,capture))
  saved=r._DEADLINE;r._DEADLINE=time.monotonic()+45
  try:pilot.cleanup_generator(job['metadata']['name'],cell.namespace,path,verified)
  except Exception as exc:r.write(path/'generator-cleanup.error',str(exc))
  finally:r._DEADLINE=saved
  manifest.update(finished_at=r.utc());r.write(path/'manifest.json',manifest)
  r.checksums(path)
 if error:raise error
 # All fixed query windows are exported after drain, without shifting bounds.
 with measure('timing-evidence-reads'):
  pilot.export_window(cell,path,manifest['start'],manifest['end'],spec['duration_seconds'])
  from .semantics_pilot import export_parity
  export_parity(cell,path,manifest['start'],manifest['end'],spec['duration_seconds'])
  export_intervals(cell,path,manifest)
 if spec['kind']=='confirmation':
  from .verification_v4 import evaluate_window
  manifest.update(phase='complete');r.write(path/'manifest.json',manifest)
  original=evaluate_window(path,r.CONFIG['thresholds']);r.write(path/'verification.json',original)
 manifest.update(phase='complete');r.write(path/'manifest.json',manifest)
 report=analyze_window(path);r.write(path/'timing-verification.json',report);r.checksums(path)
 if not report.get('independent_valid',report.get('evidence',{}).get('valid',False)):
  raise RuntimeError('required timing evidence invalid: '+str(path))
 return report

def setup_cell(cell,root,tag,with_agent=False):
 path=root/'measured'/cell.slug;path.mkdir(parents=True,exist_ok=False)
 status=dict(cell=cell.slug,phase='reset',started_at=r.utc(),windows=[]);r.write(path/'status.json',status)
 r.reset_cell(False);r.prepare_source(cell.source);r.create_namespace(cell)
 status['phase']='demo';r.write(path/'status.json',status);r.install_demo(cell,path)
 pilot.install_observers(cell,path,include_agent=with_agent)
 status['phase']='karthos';r.write(path/'status.json',status);r.deploy_karthos(cell,path)
 ids=load(path/'locked-image-ids.json');pilot.lock_runtime_images(root,ids['demo']+ids['karthos'])
 from .semantics_pilot import validate_platform_images,run_warmup
 validate_platform_images(ids['karthos'],tag);pilot.install_scripts(cell)
 status['warmup']=run_warmup(cell,path,root.name);pilot.sleep_bounded(r.CONFIG['telemetry_drain_seconds'])
 return path,status

def analyse(root):
 root=Path(root)
 if not (root/'run-report.json').exists() or load(root/'run-report.json').get('protocol')!=PROTOCOL:
  raise ValueError('analyse only accepts a separately identified timing-study root; historical results are immutable')
 from .timing_analysis import analyze_window
 rows=[]
 for manifest in sorted(root.glob('measured/*/windows/*/manifest.json')):
  p=manifest.parent
  try:
   result=analyze_window(p);r.write(p/'timing-verification.json',result)
   rows.append(dict(path=str(p.relative_to(root)),state=result.get('state'),independent_valid=result.get('independent_valid'),
     report_path=str((p/'timing-verification.json').relative_to(root)),report_sha256=digest(p/'timing-verification.json'),schema_version=result.get('schema_version')))
  except Exception as exc:rows.append(dict(path=str(p.relative_to(root)),state='incomplete',error=str(exc)))
 report=dict(protocol=PROTOCOL,summary_format='report-references1',planned_windows=16,exported_windows=len(rows),windows=rows,
  definitions='Eventual completeness and fixed-window fidelity remain separate; start/completion populations retained.')
 r.write(root/'timing-summary.json',report)
 valid=[x for x in rows if x.get('independent_valid') is True]
 decisions=dict(protocol=PROTOCOL,independent_evidence_windows=len(valid),planned_windows=16,
   measurement_contract='two verdicts; start and completion populations separate; original bounds retained',
   primary_generator=dict(decision='retain k6 and matched Locust',reason='tool identity remains confounded with realistic arrival/burst model; probes provide a separate scheduling control'),
   source_modes=dict(decision='retain traces and Istio',reason='source failure meanings and emission paths remain distinct'),
   window_duration=dict(decision='provisional300seconds',reason='diagnostic confirmation does not establish an optimal duration'),
   main_demo=dict(decision='not selected from pass counts',required_evidence='accuracy, deployed footprint, evidence cost and dependency reproducibility'),
   uncertainty_policy='first-seen intervals are visibility bounds; API/storage parity is not event-window equality',
   gke_protocol_can_freeze=False,
   remaining_actions=['Review timing explanations and unresolved boundary evidence before choosing primary event/query window claims.',
     'Summarize observed generator/collector/evaluation resource overhead; a causal overhead claim needs an uninstrumented control.',
     'Select demo and cloud node plan after footprint, current pricing and managed-cluster compatibility checks.',
     'Keep agent dependency uncertainty separate from checkout protocol readiness.'],
   cloud_resources_provisioned=False,numerical_disagreements_are_retained=True)
 r.write(root/'gke-decisions.json',decisions)
 text=['# Four-cell timing study','',f'Analysed windows: {len(rows)}/16.','',
  '| Window | Result | Independent evidence |','|---|---|---|']
 for x in rows:text.append(f"| {x['path']} | {x.get('state')} | {x.get('independent_valid','unavailable')} |")
 text+=['','Full values, population differences, availability bounds and unresolved evidence are in timing-summary.json. No cloud resources provisioned.']
 r.write(root/'SUMMARY.md','\n'.join(text)+'\n');r.checksums(root)
 return report

def run(args):
 safe_run_id(args.run_id);root=r.OUT/args.run_id;root.mkdir(parents=True,exist_ok=False)
 baseline=r.OUT/DEFAULT_BASELINE
 report=dict(protocol=PROTOCOL,state='preparing',started_at=r.utc(),windows=[],planned=plan())
 r.write(root/'run-report.json',report)
 pins=preparation(baseline,root)
 inherited=load(baseline/'locked-config.json');r.CONFIG.clear();r.CONFIG.update(inherited,study_deadline_seconds=LIVE_SECONDS)
 r.write(root/'locked-config.json',r.CONFIG);r.write(root/'locked-harness.json',pilot.resume_fingerprint())
 shutil.copytree(r.ROOT/'evaluation',root/'locked-harness',ignore=shutil.ignore_patterns('__pycache__'))
 shutil.copyfile(baseline/'runtime-image-lock.json',root/'runtime-image-lock.json')
 shutil.copyfile(baseline/'frozen-thresholds.json',root/'frozen-thresholds.json')
 report.update(state='preflight',live_started_at=r.utc(),deadline_at=(datetime.now(timezone.utc)+timedelta(seconds=LIVE_SECONDS)).isoformat())
 r._DEADLINE=time.monotonic()+LIVE_SECONDS;r.write(root/'run-report.json',report)
 def interrupt(signum,frame):raise KeyboardInterrupt('study interrupted; export and ownership-safe restoration requested')
 signal.signal(signal.SIGINT,interrupt);signal.signal(signal.SIGTERM,interrupt)
 frozen=pilot.resume_fingerprint()
 try:
  report['preflight']=r.preflight(False)
  actual=r.stage()
  if any(actual.get(k)!=pins.get(k) for k in pins):raise RuntimeError('deployment pins differ from validated baseline')
  tag=pilot.select_platform_tag(pins);report['pins']=pins;r.write(root/'run-report.json',report)
  for cell in cells_for_repeat(1):
   reserve_budget(300,r._DEADLINE-time.monotonic())
   if pilot.resume_fingerprint()!=frozen:raise RuntimeError('harness changed during study')
   pilot.assert_pins(pins)
   report.update(state='running',active_cell=cell.slug,active_window='setup');r.write(root/'run-report.json',report)
   path,status=setup_cell(cell,root,tag,with_agent=cell.demo=='astronomy')
   try:
    for spec in [s for s in plan() if s['cell']==cell.slug]:
     report['active_window']=spec['name'];r.write(root/'run-report.json',report)
     status['phase']=spec['name'];r.write(path/'status.json',status)
     result=run_window(cell,path,spec,root.name)
     status['windows'].append(dict(name=spec['name'],state=result.get('state'),independent_valid=result.get('independent_valid')))
     report['windows'].append(dict(cell=cell.slug,**status['windows'][-1]));r.write(root/'run-report.json',report)
    status.update(phase='complete',finished_at=r.utc())
   except BaseException as exc:
    status.update(phase='incomplete',error=str(exc),finished_at=r.utc());raise
   finally:r.write(path/'status.json',status);r.checksums(path)
  report['agent_diagnosis']=agent_diagnosis(root)
  report['state']='complete'
 except BaseException as exc:
  report.update(state='incomplete',error=str(exc));raise
 finally:
  report.update(finished_at=r.utc());r.write(root/'run-report.json',report)
  try:analyse(root)
  finally:r._DEADLINE=None
  print('Timing evidence:',root,flush=True)

def agent_diagnosis(root):
 # The retained failure is a separate application case. A single live prompt
 # is attempted only if the Astronomy namespace remains deployed and budget fits.
 begin=time.monotonic();remaining=r._DEADLINE-begin
 if remaining<900+300:return dict(state='not-attempted',reason='reserved export/restoration budget',cause='historical LLM301 -> APIStatusError -> prompt500')
 from .timing_agent import diagnose
 return diagnose(root/'agent-diagnosis',min(900.,remaining-300))

def main(argv=None):
 p=argparse.ArgumentParser(description=__doc__);commands=p.add_subparsers(dest='command',required=True)
 commands.add_parser('plan');run_parser=commands.add_parser('run');run_parser.add_argument('--run-id',required=True)
 a=commands.add_parser('analyse');a.add_argument('directory',type=Path)
 args=p.parse_args(argv)
 if args.command=='plan':print(json.dumps(dict(protocol=PROTOCOL,live_budget_seconds=LIVE_SECONDS,windows=plan()),indent=2))
 elif args.command=='analyse':analyse(args.directory)
 else:
  try:run(args)
  except BaseException as exc:
   root=r.OUT/args.run_id
   if (root/'run-report.json').exists():
    report=load(root/'run-report.json');report.update(state='incomplete',error=str(exc),finished_at=r.utc());r.write(root/'run-report.json',report);r.checksums(root)
   raise
if __name__=='__main__':main()
