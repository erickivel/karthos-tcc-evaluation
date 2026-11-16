"""Versioned offline interpretations; never rewrites sealed execution evidence."""
import os,hashlib,json
from pathlib import Path
from collections import Counter
from .models import read,write,sha,identifier,implementation_fingerprint,RECOVERABLE_SCHEDULES
from .coverage_report import build as build_coverage, render as render_coverage

ANALYSIS_INLINE_BYTES=8*1024**2


def _read_window_report(path,expected_sha256):
    path=Path(path)
    if path.stat().st_size<=ANALYSIS_INLINE_BYTES:
        payload=path.read_bytes()
        if hashlib.sha256(payload).hexdigest()!=expected_sha256:raise ValueError('report checksum differs')
        result=json.loads(payload)
        if 'compact_report' in result:
            from .compact_report import expand_report
            return expand_report(result,lambda name:(path.parent/name).open('rb'))
        return result
    try:from .analysis_reader import read_analysis_report
    except ImportError as exc:raise RuntimeError('large-report analysis requires requirements-analysis.txt in the operator Python environment') from exc
    with path.open('rb') as stream:
        result,_=read_analysis_report(stream,expected_sha256,open_member=lambda name:(path.parent/name).open('rb'))
    return result


def _resolve_window_report(source,row):
    """Select bound terminal analysis from restored bundles, never fetch raw."""
    from .archive_inputs import analysis_status_state
    source=Path(source);receipt=row.get('export',{});late=receipt.get('analysis_export')
    def unique(pattern):
        candidates=list(source.rglob(pattern))
        if len(candidates)>1:raise ValueError('ambiguous window evidence '+row['id'])
        return candidates[0] if candidates else None
    def load_bound(path):
        digest=sha(path)
        if late.get('members',{}).get(path.name,{}).get('sha256')!=digest:
            raise ValueError('analysis report receipt checksum differs')
        return _read_window_report(path,digest)
    if late is not None:
        if late.get('verified') is not True:raise ValueError('analysis export is not verified')
        status_path=unique(row['id']+'/analysis/analysis-status.json')
        if status_path is None:return None,None
        status=load_bound(status_path)
        original={key:value for key,value in receipt.items() if key!='analysis_export'}
        if status.get('raw_evidence_export')!=original:raise ValueError('analysis status raw-evidence binding differs from run receipt')
        worker_path=status_path.parent/'worker-status.json'
        worker=load_bound(worker_path) if worker_path.is_file() else None
        if analysis_status_state(status,worker)=='failed':return status_path,status
        path=unique(row['id']+'/analysis/gke-evaluation.json')
        if path is None:return None,None
        evidence=load_bound(path)
        if evidence.get('raw_evidence_export')!=original:raise ValueError('analysis raw-evidence binding differs from run receipt')
        if evidence.get('analysis',{}).get('state')!='complete':raise ValueError('report and authoritative analysis status differ')
        return path,evidence
    path=unique(row['id']+'/gke-evaluation.json')
    return (path,_read_window_report(path,sha(path))) if path else (None,None)


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
        path,evidence=_resolve_window_report(source,row)
        if path:
            digest=sha(path);inputs[str(path.relative_to(root))]=digest
            windows.append({'id':row['id'],'analysis':evidence.get('analysis'),'raw_evidence_export':evidence.get('raw_evidence_export'),'execution':evidence.get('execution'),'required_evidence':evidence.get('evidence'),'numerical_agreement':evidence.get('numerical_agreement'),'families':evidence.get('families',{}),'boundary_summary':evidence.get('boundary_summary'),'coverage':evidence.get('coverage'),'consumer':evidence.get('consumer'),'protocol':evidence.get('protocol'),'evidence_directory':str((path.parent.parent if path.parent.name=='analysis' else path.parent).relative_to(root)),'metric_window_coverage':evidence.get('metric_window_coverage') or build_coverage(evidence.get('coverage') or {},(evidence.get('traffic') or {}).get('report') or {},start=(evidence.get('manifest') or {}).get('start'),end=(evidence.get('manifest') or {}).get('end'),checks=(evidence.get('numerical_agreement') or {}).get('checks',[]),resources=evidence.get('resources')),'original_report_sha256':digest})
        else:windows.append({'id':row['id'],'execution':row.get('execution'),'required_evidence':{'valid':False,'reason':'final bound analysis report not available in this export' if row.get('export',{}).get('analysis_export') else 'raw window report not available in this export'},'numerical_agreement':{'state':'insufficient'}})
    result={'schema_version':1,'analysis_id':analysis_id,'run_id':run_id,'verifier_version':'gke-qualification-v1-analysis-2','implementation_sha256':implementation_fingerprint(root),'original_inputs':inputs,'run_state':run.get('state'),'logical_windows':run.get('records',[]),'history':run.get('history'),'readiness':run.get('readiness'),'windows':windows,'execution_verdict_counts':dict(Counter(str(w.get('execution')) for w in windows)),'original_results_modified':False,'note':'Interpretation of available exported reports; absent raw evidence is insufficient. No numeric disagreement is converted to missing execution.'}
    claims=source/'claim-matrix.json'
    if claims.is_file():
        inputs[str(claims.relative_to(root))]=sha(claims)
        result['claim_matrix']=read(claims)
        result['verifier_version']='thesis-qualification-v2-analysis-1'
    if any(w.get('protocol')=='thesis-qualification-v3' for w in windows):
        result['verifier_version']='thesis-qualification-v3-analysis-1'
    if any(w.get('protocol')=='thesis-qualification-v4' for w in windows):
        result['verifier_version']='thesis-qualification-v4-analysis-1'
    if run.get('protocol') in ('thesis-repair-validation-v1','thesis-repair-validation-v2','thesis-completion-v1','thesis-completion-v2'):result['verifier_version']=run['protocol']+'-analysis-1'
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
    from .boundary_summary import render as render_boundary
    import html
    links=[]
    for window in windows:
        if window.get('boundary_summary'):
            if Path(window['id']).name!=window['id'] or window['id'] in ('.','..'):raise ValueError('unsafe boundary window ID')
            name='boundaries/'+window['id']+'.html'
            write(destination/name,render_boundary(window['boundary_summary'],title=window['id']))
            links.append('<li><a href="'+html.escape(name,quote=True)+'">'+html.escape(window['id'])+'</a></li>')
    write(destination/'index.html','<!doctype html><meta charset="utf-8"><title>Thesis analysis</title>'
          '<h1>Thesis analysis</h1><p>Versioned interpretation; original execution evidence is unchanged.</p>'
          '<p><a href="metric-window-coverage.html">Metric populations and coverage</a> · '
          '<a href="analysis.json">Complete analysis and input checksums</a></p><h2>Boundary explanations</h2><ul>'
          +''.join(links)+'</ul>')
    write(destination/'README.md','# Offline analysis '+analysis_id+'\n\nOriginal execution: '+run_id+'. This version references original input checksums in analysis.json. It does not replace historical results.\n')
    return {'analysis':str(destination),'windows':len(windows),'original_results_modified':False}
