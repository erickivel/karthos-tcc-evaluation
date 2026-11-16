"""One bounded read of controller progress; no sampling loop or completion claims."""
import json
import sys
from pathlib import Path
from .models import identifier

JSON_LIMIT=8*1024**2
TAIL_LIMIT=64*1024


def _json(path):
    if path.is_symlink():raise ValueError('symlink progress file refused')
    with path.open('rb') as source:raw=source.read(JSON_LIMIT+1)
    if len(raw)>JSON_LIMIT:raise ValueError('progress JSON exceeds 8 MiB bound')
    return json.loads(raw)


def collect(run_id, *, base=Path('/data/results/gke-qualification-v1/runs')):
    path=Path(base)/identifier(run_id)
    if path.is_symlink():raise ValueError('symlink run directory refused')
    result={'run_id':run_id,'completed_windows':None,'finished_windows':None,'remaining_windows':None,'planned_windows':None,
            'failed_windows':None,'failed_attempts':None,'failures':[],'last_phase':None,
            'report_state':'unknown','controller_failure':None,'errors':{},
            'terminal_state':'unknown','keep_observing':True,
            'scope':'bounded controller files; final seal and provider cleanup not verified'}
    try:
        matrix=_json(path/'matrix.json')
        if not isinstance(matrix,list) or not all(isinstance(row,dict) and isinstance(row.get('id'),str) for row in matrix):
            raise ValueError('matrix must contain window objects with ids')
        if len({row['id'] for row in matrix})!=len(matrix):raise ValueError('duplicate matrix window id')
        failures=[]
        for row in matrix:
            attempts=row.get('attempts',[]) or [row]
            if not isinstance(attempts,list) or not all(isinstance(a,dict) for a in attempts):
                raise ValueError('invalid matrix attempts')
            for attempt in attempts:
                if attempt.get('state')=='failed' or attempt.get('execution')=='incomplete':
                    failures.append({'window':row['id'],'attempt':attempt.get('attempt_id',row['id']),
                                     'state':attempt.get('state'),'execution':attempt.get('execution'),
                                     'reason':str(attempt.get('reason',''))[:1000]})
        completed=sum(row.get('state')=='complete' for row in matrix)
        finished=sum(row.get('retry_policy_complete') is True or row.get('state')=='complete' for row in matrix)
        result.update(planned_windows=len(matrix),completed_windows=completed,finished_windows=finished,remaining_windows=len(matrix)-finished,
                      failed_windows=sum(row.get('state')=='failed' for row in matrix),failed_attempts=len(failures),
                      failures=failures[:100],failures_truncated=len(failures)>100)
    except (OSError,ValueError,TypeError) as exc:result['errors']['matrix']=str(exc)[:1000]
    try:
        report=_json(path/'run-report.json')
        if not isinstance(report,dict):raise ValueError('run report must be an object')
        result['report_state']=str(report.get('state','unknown'))[:100]
        if report.get('failure'):result['controller_failure']=json.dumps(report['failure'],sort_keys=True)[:2000]
    except (OSError,ValueError,TypeError) as exc:result['errors']['report']=str(exc)[:1000]
    try:
        progress=path/'progress.ndjson'
        if progress.is_symlink():raise ValueError('symlink progress file refused')
        with progress.open('rb') as source:
            source.seek(0,2);length=source.tell();source.seek(max(0,length-TAIL_LIMIT));tail=source.read(TAIL_LIMIT)
        lines=tail.splitlines()
        if length>TAIL_LIMIT:lines=lines[1:]  # first tail line may be partial
        for line in reversed(lines):
            try:event=json.loads(line)
            except (ValueError,UnicodeError):continue
            if isinstance(event,dict) and isinstance(event.get('phase'),str) and isinstance(event.get('at'),(int,float,str)):
                result['last_phase']={'at':event['at'],'phase':event['phase'][:200]};break
        if result['last_phase'] is None:raise ValueError('no complete progress event in bounded tail')
    except (OSError,ValueError) as exc:result['errors']['progress']=str(exc)[:1000]
    return result


if __name__=='__main__':print(json.dumps(collect(sys.argv[1])))
