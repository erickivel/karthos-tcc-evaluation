"""Versioned offline interpretations; never rewrites sealed execution evidence."""
import os
from pathlib import Path
from collections import Counter
from .models import read,write,sha,identifier,implementation_fingerprint,RECOVERABLE_SCHEDULES
from .coverage_report import build as build_coverage, render as render_coverage

def analyse(root,run_id,analysis_id):
    root=Path(root).resolve();identifier(run_id);identifier(analysis_id)
    source=root/'results/gke-qualification-v1/runs'/run_id;report=source/'run-report.json'
    if not report.exists():raise ValueError('run report unavailable; no execution is inferred')
    destination=root/'analyses/gke-qualification-v1'/run_id/analysis_id
    if destination.exists():raise FileExistsError('analysis IDs are immutable')
    run=read(report);windows=[];inputs={str(report.relative_to(root)):sha(report)}
    execution_rows=run.get('windows',run.get('records',[]))
    if run.get('protocol') in RECOVERABLE_SCHEDULES:
        execution_rows=[dict(attempt,id=attempt['attempt_id'],logical_id=row['id']) for row in execution_rows for attempt in row.get('attempts',[])]
    for row in execution_rows:
        candidates=list(source.rglob(row['id']+'/gke-evaluation.json'))
        if len(candidates)>1:raise ValueError('ambiguous window evidence '+row['id'])
        path=candidates[0] if candidates else None
        if path:
            evidence=read(path);inputs[str(path.relative_to(root))]=sha(path)
            windows.append({'id':row['id'],'execution':evidence.get('execution'),'required_evidence':evidence.get('evidence'),'numerical_agreement':evidence.get('numerical_agreement'),'coverage':evidence.get('coverage'),'consumer':evidence.get('consumer'),'protocol':evidence.get('protocol'),'evidence_directory':str(path.parent.relative_to(root)),'metric_window_coverage':evidence.get('metric_window_coverage') or build_coverage(evidence.get('coverage') or {},(evidence.get('traffic') or {}).get('report') or {},start=(evidence.get('manifest') or {}).get('start'),end=(evidence.get('manifest') or {}).get('end'),checks=(evidence.get('numerical_agreement') or {}).get('checks',[]),resources=evidence.get('resources')),'original_report_sha256':sha(path)})
        else:windows.append({'id':row['id'],'execution':row.get('execution'),'required_evidence':{'valid':False,'reason':'raw window report not available in this export'},'numerical_agreement':{'state':'insufficient'}})
    result={'schema_version':1,'analysis_id':analysis_id,'run_id':run_id,'verifier_version':'gke-qualification-v1-analysis-1','implementation_sha256':implementation_fingerprint(root),'original_inputs':inputs,'run_state':run.get('state'),'logical_windows':run.get('records',[]),'history':run.get('history'),'readiness':run.get('readiness'),'windows':windows,'execution_verdict_counts':dict(Counter(str(w.get('execution')) for w in windows)),'original_results_modified':False,'note':'Interpretation of available exported reports; absent raw evidence is insufficient. No numeric disagreement is converted to missing execution.'}
    claims=source/'claim-matrix.json'
    if claims.is_file():
        inputs[str(claims.relative_to(root))]=sha(claims)
        result['claim_matrix']=read(claims)
        result['verifier_version']='thesis-qualification-v2-analysis-1'
    if any(w.get('protocol')=='thesis-qualification-v3' for w in windows):
        result['verifier_version']='thesis-qualification-v3-analysis-1'
    if any(w.get('protocol')=='thesis-qualification-v4' for w in windows):
        result['verifier_version']='thesis-qualification-v4-analysis-1'
    if run.get('protocol')=='thesis-repair-validation-v1':result['verifier_version']='thesis-repair-validation-v1-analysis-1'
    result['coverage_schema_version']='metric-window-coverage-v1'
    coverage_rows=[]
    for window in windows:
        for row in (window.get('metric_window_coverage') or {}).get('rows', []):
            links=[]
            for link in row.get('evidence_links', []):
                file, separator, fragment=link.partition('#')
                relative=os.path.relpath(root/window['evidence_directory']/file, destination)
                links.append(relative+(separator+fragment if separator else ''))
            coverage_rows.append(dict(row,execution_window=window['id'],window=window['id']+'/'+str(row['window']),evidence_links=links))
    result['metric_window_coverage']={'schema_version':result['coverage_schema_version'],'rows':coverage_rows}
    destination.mkdir(parents=True);write(destination/'analysis.json',result)
    coverage_csv,coverage_html=render_coverage(coverage_rows)
    write(destination/'metric-window-coverage.csv',coverage_csv)
    write(destination/'metric-window-coverage.html',coverage_html)
    write(destination/'README.md','# Offline analysis '+analysis_id+'\n\nOriginal execution: '+run_id+'. This version references original input checksums in analysis.json. It does not replace historical results.\n')
    return {'analysis':str(destination),'windows':len(windows),'original_results_modified':False}
