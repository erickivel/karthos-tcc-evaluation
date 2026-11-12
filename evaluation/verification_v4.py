"""Protocol4 reporting separates evidence quality from numerical/application quality."""
from __future__ import annotations

import copy

from . import evidence_v4


def assemble_verdicts(report, structured):
    report=copy.deepcopy(report)
    native=report['manifest'].get('mode')=='native'
    artifact=report.get('generator_artifacts') or {}
    execution_complete=bool(artifact.get('valid') and not report['manifest'].get('interrupted')
                            and report['manifest'].get('phase')!='incomplete')
    capture_valid=bool(report.get('capture',{}).get('valid') and structured.get('valid'))
    if not native and report.get('source',{}).get('unpaired'):
        report['source_valid']=False
        report['oracle_valid']=False
        reason='unpaired checkout SERVER spans prevent complete source accounting'
        if reason not in report['reasons']:report['reasons'].append(reason)
    boundary_valid=bool(report.get('native_boundaries',{}).get('valid')) if native else True
    valid=execution_complete and capture_valid and boundary_valid and (native or (report.get('oracle_valid') and report.get('source_valid')))
    report['protocol_version']=4;report['verifier_version']='4.1.1'
    report['execution']=dict(state='complete' if execution_complete else 'incomplete',
        workload_exit_failed=report['generator'].get('job_failed'),
        interpretation='closed verified artifacts establish execution; application failures remain separate')
    report['evidence']=dict(valid=valid,state='pass' if valid else 'incomplete',
                            structured_capture=structured,capture_valid=capture_valid,boundary_valid=boundary_valid)
    report['application_outcomes']=dict(
        state='descriptive' if native else ('fail' if report['generator'].get('unexpected_outcomes') else 'pass'),
        unexpected=None if native else report['generator'].get('unexpected_outcomes'),
        unclassified=report['generator'].get('requests',0) if native else 0,
        note='Native outcomes require declared route rules; HTTP results alone are not business success')
    source_count=report.get('source',{}).get('requests');stored_count=report.get('vm',{}).get('requests')
    if source_count is not None and stored_count is not None and source_count!=stored_count:
        report['duration']['population']=dict(state='incomplete',source_count=source_count,stored_count=stored_count,
             reason='observed counts differ; duration population equivalence is not established')
        for key in ('source_to_vm','source_to_api'):
            if key in report['duration']:
                report['duration'][key]=dict(report['duration'][key],numerical_difference_state=report['duration'][key]['state'],
                    state='incomplete',reason='different or unproven duration populations; values retained')
        for row in report.get('stage_table',[]):
            if row.get('stage') in ('source_duration_to_vm','source_duration_to_api'):
                row.update(state='incomplete',reason='different or unproven duration populations')
    from .scoring import state
    numerical=[]
    for name in ('frontend','resources','entities','runs_on_edges'):
        if report.get(name):numerical.append(report[name])
    numerical.extend(check for check in report.get('counts_path',{}).values() if not check.get('diagnostic_only'))
    numerical.extend(report['duration'][name] for name in ('api','source_to_vm') if name in report['duration'])
    if report.get('vm',{}).get('histogram'):numerical.append(report['vm']['histogram'])
    report['numerical_agreement']=dict(state=state(numerical) if numerical else 'incomplete',
        note='Eligible boundary checks after population validation; application outcomes and evidence validity are separate')
    if native:
        report['state']='descriptive' if valid else 'incomplete'
        report['generator']=dict(report['generator'],unexpected_outcomes=None)
        report['native_evidence']=dict(report.get('native_evidence',{}),complete=valid,request_ledger_required=True)
        report['numerical_agreement']['state']='descriptive'
    else:
        report['oracle_valid']=bool(report.get('oracle_valid') and capture_valid)
        checks=[report['numerical_agreement'],report['application_outcomes'],
                dict(state='pass' if valid else 'incomplete')]
        dropped=report['generator'].get('dropped_iterations')
        if dropped:checks.append(dict(state='fail',reason='generator dropped scheduled work'))
        elif report['manifest'].get('generator')=='k6' and dropped is None:
            checks.append(dict(state='incomplete',reason='missing generator scheduling evidence'))
        report['state']=state(checks)
    report['stage_table'].append(dict(stage='structured_capture',state='pass' if structured.get('valid') else 'incomplete'))
    return report


def evaluate_window(path, thresholds):
    from . import scoring
    def resource_provider(*args,**kwargs):
        return evidence_v4.resource_scores(path,*args,**kwargs)
    report=scoring._evaluate_window(path,thresholds,resource_provider=resource_provider,
                                    trace_provider=evidence_v4.trace_source)
    if report['manifest'].get('mode')=='native':
        from .native_boundaries import native_boundary_scores
        boundary=native_boundary_scores(path,report['manifest'])
        report['native_boundaries']=boundary
        report['frontend']=boundary['frontend']
        for stage in report.get('stage_table',[]):
            if stage.get('stage')=='generator_to_frontend':
                stage.update(state=boundary['frontend']['state'],expected=boundary['frontend'].get('expected'),observed=boundary['frontend']['observed'])
        report['stage_table'].append(dict(stage='generator_to_boundaries',state=boundary['state'],valid=boundary['valid']))
    structured=scoring.read(path/'structured/status.json',{})
    return assemble_verdicts(report,structured)
