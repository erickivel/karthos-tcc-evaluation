"""Protocol4 repair-validation campaign; no live action without readiness proof."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .protocol import Cell, cells_for_repeat, source_id

PROTOCOL_VERSION = 4


def generator_order(cell):
    first = ('k6','locust') if cell.demo == 'boutique' else ('locust','k6')
    return first if cell.repeat == 1 else tuple(reversed(first))


def schedule():
    result=[]
    def append(phase,cell,generator,duration,rate,workload='checkout-heavy'):
        result.append(dict(phase=phase,cell=cell,spec=dict(
             name=f'{generator}-{workload}',generator=generator,workload=workload,
             duration_seconds=duration,rate=rate,protocol_version=4)))
    for cell in cells_for_repeat(1):
        for generator in generator_order(cell):append('smoke',cell,generator,60,2)
    for repeat in (1,2):
        for cell in cells_for_repeat(repeat):
            for generator in generator_order(cell):append('measured',cell,generator,300,2)
            if repeat == 1:append('measured',cell,'locust',180,0,'native')
    for demo in ('boutique','astronomy'):
        cell=Cell(demo,'trace',1)
        for generator in generator_order(cell):append('diagnostics',cell,generator,300,4)
    return result


def serializable_plan():
    return [dict(phase=row['phase'],cell=row['cell'].slug,demo=row['cell'].demo,
                 source=row['cell'].source,repeat=row['cell'].repeat,**row['spec']) for row in schedule()]


def prime_after_instrumentation(cell,path,study_id,image_lock_root):
    """Prime fresh collector counters before preroll; never shift a measured window."""
    from . import runner as r
    from .semantics_pilot import run_warmup
    result=run_warmup(cell,path,study_id+':capture-priming',image_lock_root=image_lock_root)
    result=dict(result,purpose='counter_priming_after_collector_restart',included_in_measurement=False)
    r.write(Path(path)/'counter-priming.json',result)
    return result


def window_bounds(start, actual_end, duration_seconds, ledger):
    from .scoring import stamp
    nominal=stamp(start)+duration_seconds
    actual=max([stamp(actual_end),nominal]+[stamp(r['completed_at']) for r in ledger])
    end=datetime.fromtimestamp(nominal,timezone.utc).isoformat().replace('+00:00','Z')
    return dict(start=start,end=end,scheduled_end=end,actual_workload_end=actual_end,
        completion_tail_seconds=max(0,actual-nominal),
        in_flight_completions=sum(stamp(r['started_at'])<nominal<stamp(r['completed_at']) for r in ledger),
        requests_started_after_scheduled_end=sum(stamp(r['started_at'])>=nominal for r in ledger),
        observation_seconds=duration_seconds,
        window_semantics='fixed scheduled arrival window; completion tail and telemetry drain retained separately')


def evidence_files_issues(records, root):
    issues=[]
    if not records:return ['missing evidence references']
    for item in records:
        p=Path(item.get('path',''))
        if not p.is_absolute():p=Path(root)/p
        expected=item.get('sha256')
        if not p.is_file() or not expected or hashlib.sha256(p.read_bytes()).hexdigest()!=expected:
            issues.append('missing or changed evidence: '+str(p))
    return issues


def recurrence_readiness_issues(cause, root):
    """User-approved prospective gate; never asserts a historical root cause."""
    root=Path(root);issues=[]
    if cause.get('readiness_policy')!='user-approved-recurrence-v1' or cause.get('historical_cause')!='unknown':
        issues.append('recurrence readiness must retain the original cause as unknown')
    decision=cause.get('decision',{})
    if not isinstance(decision,dict):return issues+['missing hashed readiness decision']
    decision_issues=evidence_files_issues([decision],root)
    issues.extend(decision_issues)
    if not decision_issues:
        try:
            path=Path(decision['path']);path=path if path.is_absolute() else root/path
            approved=json.loads(path.read_text())
            if approved.get('approval')!='recurrence_based_readiness' or not approved.get('user_reply'):
                issues.append('recurrence readiness lacks the recorded user decision')
        except (OSError,ValueError,KeyError,TypeError):issues.append('invalid readiness decision record')
    attempts=cause.get('attempts',[])
    if not isinstance(attempts,list) or len(attempts)!=3:
        return issues+['recurrence readiness requires three separately exported attempts']
    run_ids=set();window_paths=set()
    for number,attempt in enumerate(attempts,1):
        prefix=f'recurrence attempt {number}: '
        if not isinstance(attempt,dict) or any(not isinstance(attempt.get(k),dict) for k in ('manifest','verification','ledger','artifact_manifest')):
            issues.append(prefix+'missing manifest, verification, ledger or generator checksums');continue
        file_issues=evidence_files_issues([attempt[k] for k in ('manifest','verification','ledger','artifact_manifest')],root)
        if file_issues:issues.extend(prefix+x for x in file_issues);continue
        try:
            def load(kind, lines=False):
                path=Path(attempt[kind]['path']);path=path if path.is_absolute() else root/path
                return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if lines else json.loads(path.read_text())
            def resolved(kind):
                path=Path(attempt[kind]['path']);return (path if path.is_absolute() else root/path).resolve()
            window=resolved('manifest').parent
            if (window in window_paths or resolved('verification').parent!=window or resolved('ledger').parent!=window
                or resolved('artifact_manifest')!=window/'generator-artifacts'/'SHA256SUMS'):
                issues.append(prefix+'evidence is not a distinct, colocated exported window');continue
            window_paths.add(window)
            manifest,score,ledger=load('manifest'),load('verification'),load('ledger',True)
            if score.get('manifest')!=manifest:
                issues.append(prefix+'verification does not identify the supplied manifest')
            required=dict(demo='astronomy',source='mesh',generator='k6',workload='checkout-heavy',duration_seconds=60,rate=2,protocol_version=4,generator_artifacts_complete=True)
            if any(manifest.get(k)!=v for k,v in required.items()):issues.append(prefix+'unexpected configuration or incomplete generator artifacts')
            run_id=manifest.get('run_id')
            if not isinstance(run_id,str) or not run_id or run_id in run_ids:issues.append(prefix+'missing or reused run identity')
            run_ids.add(run_id)
            from .generator_artifacts import validate_export
            from .pilot import extract_events
            raw=window/'generator-artifacts'
            validate_export(raw,'k6','matched',run_id)
            original_log=(raw/'generator.log').read_text()
            original_ledger=extract_events(original_log,'REQUEST')
            markers=extract_events(original_log,'WINDOW')
            if ledger!=original_ledger:
                issues.append(prefix+'request ledger differs from the complete generator log')
            if any(markers[0].get(k)!=v for k,v in dict(intended_duration_seconds=60,target_iterations_per_second=2,expected_iterations=120).items()):
                issues.append(prefix+'generator did not declare the required schedule')
            from .scoring import stamp
            import math
            def timestamp(value):
                if isinstance(value,bool):raise ValueError('boolean timestamp')
                result=stamp(value)
                if not math.isfinite(result):raise ValueError('nonfinite timestamp')
                return result
            start,end=timestamp(manifest['start']),timestamp(manifest['end'])
            if start!=timestamp(markers[0]['started_at']) or end!=start+60:
                issues.append(prefix+'scoring bounds differ from the fixed generator schedule')
            population=[row for row in ledger if row.get('run_id')==run_id and start<=timestamp(row['started_at'])<end]
            if any(score.get('generator',{}).get(k)!=len(population) for k in ('requests','completed_requests')):
                issues.append(prefix+'scored request population differs from the fixed-window ledger')
            if (score.get('oracle_valid') is not True or score.get('source_valid') is not True
                or score.get('evidence',{}).get('valid') is not True or score.get('evidence',{}).get('capture_valid') is not True
                or score.get('execution',{}).get('state')!='complete'):
                issues.append(prefix+'required execution, oracle, source or capture evidence is invalid')
            if score.get('application_outcomes',{}).get('state')!='pass' or score.get('application_outcomes',{}).get('unexpected')!=0:
                issues.append(prefix+'unexpected or unclassified application outcomes')
            ids=set();steps={};journey_starts={}
            if not ledger:issues.append(prefix+'empty request ledger')
            for row in ledger:
                request_id=row.get('request_id');status=row.get('status')
                if (not isinstance(request_id,str) or not request_id or request_id in ids or row.get('run_id')!=run_id
                    or not isinstance(status,int) or isinstance(status,bool) or not 100<=status<=599 or status==503
                    or status!=row.get('expected_status') or row.get('completed') is not True
                    or row.get('outcome_ok') is not True or row.get('exception') is not None):
                    issues.append(prefix+'request ledger contains a 503, unexpected outcome, invalid identity or incomplete request');break
                ids.add(request_id)
                iteration,index=row.get('iteration'),row.get('step_index')
                if type(iteration) is not int or iteration<0 or type(index) is not int or index<0:
                    issues.append(prefix+'missing journey identity');break
                from .workloads.common import journey
                recipe=journey('astronomy','checkout-heavy',iteration,run_id)
                if (index>=len(recipe) or row.get('route')!=recipe[index]['route']
                    or row.get('expected_status')!=recipe[index]['expected_status']
                    or index in steps.setdefault(iteration,set())):
                    issues.append(prefix+'request does not match the declared journey');break
                steps[iteration].add(index)
                if index==0:journey_starts[iteration]=timestamp(row['started_at'])
            from .workloads.common import journey
            if any(steps.get(n)!=set(range(len(journey('astronomy','checkout-heavy',n,run_id)))) for n in range(120)):
                issues.append(prefix+'planned journeys are missing requests')
            if any(n not in journey_starts or not start<=journey_starts[n]<end for n in range(120)):
                issues.append(prefix+'planned journeys did not start inside the fixed window')
        except (OSError,ValueError,KeyError,TypeError,AttributeError,OverflowError):issues.append(prefix+'malformed evidence')
    return issues


def readiness_issues(report,root=None):
    root=Path(root or '.')
    issues=[]
    for gate in ('product_ci','harness_tests','live_repairs'):
        value=report.get(gate,{})
        if value.get('state')!='pass':issues.append(gate+' has not passed')
        else:issues.extend(evidence_files_issues(value.get('evidence',[]),root))
    cause=report.get('astronomy_503',{})
    if cause.get('state')=='recurrence_validated':
        issues.extend(recurrence_readiness_issues(cause,root))
    elif cause.get('state')!='resolved' or not cause.get('initiating_cause'):
        issues.append('original Astronomy 503 initiating cause remains unsupported; mechanism alone is insufficient')
    else:
        issues.extend(evidence_files_issues(cause.get('historical_evidence',[]),root))
        issues.extend(evidence_files_issues(cause.get('verification_evidence',[]),root))
    return issues


def verify_run(root):
    from . import runner as r
    from .scoring import evaluate_window
    root=Path(root)
    manifests=[p for phase in ('smoke','measured','diagnostics') for p in (root/phase).glob('*/windows/*/manifest.json')]
    if any(json.loads(p.read_text()).get('protocol_version',0)<4 for p in manifests):
        raise ValueError('historical protocol results are immutable; use reanalyse --protocol 4 with a new output directory')
    if not manifests:
        record=root/'run-report.json'
        if not record.exists() or json.loads(record.read_text()).get('protocol_version')!=4:
            raise ValueError('not an existing protocol4 run; historical/unknown results cannot be rewritten')
    frozen=root/'frozen-thresholds.json'
    thresholds=json.loads(frozen.read_text()) if frozen.exists() else r.CONFIG['thresholds']
    windows=[]
    for phase in ('smoke','measured','diagnostics'):
        for manifest in sorted((root/phase).glob('*/windows/*/manifest.json')):
            score=evaluate_window(manifest.parent,thresholds)
            r.write(manifest.parent/'verification.json',score)
            windows.append(dict(phase=phase,cell=score['manifest'].get('cell'),
                generator=score['manifest'].get('generator'),mode=score['manifest'].get('mode'),
                state=score['state'],evidence=score.get('evidence'),execution=score.get('execution'),
                application_outcomes=score.get('application_outcomes'),path=str(manifest.parent.relative_to(root))))
            r.checksums(manifest.parent)
    summary=dict(protocol_version=4,planned_windows=32,exported_windows=len(windows),
       matrix_complete=len(windows)==32,states=dict(Counter(w['state'] for w in windows)),windows=windows,
       interpretation='Execution, evidence, numerical agreement and application outcomes are separate. Generator identity and burst shape remain confounded. Native runs are descriptive.')
    r.write(root/'summary.json',summary)
    lines=['# Protocol4 repair-validation results','',f'Exported windows: {len(windows)}/32.','',
      'Native results are descriptive. Higher-rate diagnostics are separate from the baseline repeats.','',
      '| Phase | Cell | Generator | Mode | Result |','|---|---|---|---|---|']
    lines.extend(f"| {w['phase']} | {w['cell']} | {w['generator']} | {w['mode']} | {w['state']} |" for w in windows)
    r.write(root/'SUMMARY.md','\n'.join(lines)+'\n')
    r.checksums(root)
    return summary


def main(argv=None):
    from . import runner as r,pilot
    from .semantics_pilot import resolve_study_deadline,derivation_evidence
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--show-plan',action='store_true')
    parser.add_argument('--verify',type=Path)
    parser.add_argument('--readiness',type=Path)
    parser.add_argument('--run-id')
    parser.add_argument('--smoke-only',action='store_true')
    parser.add_argument('--deadline-at')
    parser.add_argument('--replace-existing',action='store_true')
    args=parser.parse_args(argv)
    if args.show_plan:
        print(json.dumps(serializable_plan(),indent=2));return
    if args.verify:
        print(json.dumps(verify_run(args.verify),indent=2));return
    if not args.readiness:parser.error('--readiness is required before any live action')
    proof=json.loads(args.readiness.read_text())
    issues=readiness_issues(proof,args.readiness.parent)
    if proof.get('product_source_hash')!=r.repository_fingerprint(r.REPO):issues.append('product changed since readiness verification')
    if proof.get('harness_fingerprint')!=pilot.resume_fingerprint():issues.append('harness changed since readiness verification')
    if issues:raise RuntimeError('pilot prerequisites blocked: '+'; '.join(issues))
    run_id=args.run_id or datetime.now(timezone.utc).strftime('repairs-v4-%Y%m%dT%H%M%SZ')
    if Path(run_id).name!=run_id or run_id in ('.','..'):raise ValueError('run ID must be one directory name')
    budget,deadline=resolve_study_deadline(args.deadline_at,datetime.now(timezone.utc),r.CONFIG['study_deadline_seconds'])
    root=r.OUT/run_id;root.mkdir(parents=True,exist_ok=False)
    r._DEADLINE=time.monotonic()+budget
    r.CONFIG.update(protocol_version=4,servicegraph_store_max_items=10000,
                    locust_duration_seconds=180,instrumentation_budget_seconds=1200,capture_log_max_bytes=256*1024*1024)
    report=dict(protocol_version=4,state='starting',started_at=r.utc(),deadline_at=deadline,
                run_id=run_id,planned_windows=serializable_plan(),cells=[])
    r.write(root/'readiness.json',proof);r.write(root/'run-report.json',report)
    try:
        report['preflight']=r.preflight(args.replace_existing)
        report['pins']=r.stage();tag=pilot.select_platform_tag(report['pins'])
        r.write(root/'locked-config.json',r.CONFIG);r.write(root/'locked-harness.json',pilot.resume_fingerprint())
        r.shutil.copytree(r.ROOT/'evaluation',root/'locked-harness',ignore=r.shutil.ignore_patterns('__pycache__'))
        r.cmd([str(r.REPO/'test/e2e/prepare-platform-images.sh')],cwd=r.REPO,capture=root/'image-build.log',timeout=1800)
        rows=schedule()
        for phase in ('smoke','measured','diagnostics'):
            if phase=='measured':
                calibration=[dict(cell=x['cell'],valid=x['valid'],windows=x['windows']) for x in report['cells']]
                r.write(root/'frozen-thresholds.json',pilot.freeze_thresholds(r.CONFIG['thresholds'],calibration))
                if args.smoke_only:report['state']='smoke_complete';break
            cells=list(dict.fromkeys(row['cell'] for row in rows if row['phase']==phase))
            for cell in cells:
                pilot.assert_pins(report['pins'])
                specs=[row['spec'] for row in rows if row['phase']==phase and row['cell']==cell]
                report.update(active_phase=phase,active_cell=cell.slug,state='running')
                r.write(root/'run-report.json',report)
                status=pilot.run_cell(cell,root,smoke=phase=='smoke',phase_name=phase,
                    replace_existing=args.replace_existing,specs=specs,include_native=False,platform_tag=tag)
                valid=True
                for spec in specs:
                    path=root/phase/cell.slug/'windows'/spec['name']
                    score=json.loads((path/'verification.json').read_text())
                    gate=score.get('evidence',{}).get('valid',False)
                    if spec['workload']!='native':
                        gate=gate and score['oracle_valid'] and score['source_valid'] and derivation_evidence(path,source_id(cell.source))
                    r.write(path/'evidence-gate.json',dict(valid=gate,numerical_result=score['state']))
                    r.checksums(path);valid=valid and gate
                report['cells'].append(dict(cell=cell.slug,phase=phase,valid=valid,windows=status['windows']))
                if not valid:raise RuntimeError('required evidence invalid; attempts retained')
        else:report['state']='complete'
    except BaseException as exc:
        report.update(state='incomplete',error=str(exc));raise
    finally:
        try:
            report.update(finished_at=r.utc(),attempts=r.attempt_statuses(root))
            r.write(root/'run-report.json',report)
            verify_run(root)
        finally:
            r._DEADLINE=None
        print('Protocol4 evidence:',root,flush=True)


if __name__=='__main__':main()
