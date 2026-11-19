"""Observation classification only; transport uncertainty never establishes termination."""
import json


def failure_keys(snapshot):
    keys=set()
    for row in snapshot.get('matrix',[]):
        for attempt in row.get('attempts',[]) or [row]:
            if attempt.get('state')=='failed' or attempt.get('execution')=='incomplete':
                keys.add(('attempt',row['id'],attempt.get('attempt_id',row['id'])))
    for row in snapshot.get('readiness',[]):
        if row.get('usable') is False:keys.add(('readiness',row.get('id') or row.get('cell'),'unusable'))
    if snapshot.get('report',{}).get('failure'):
        keys.add(('controller',json.dumps(snapshot['report']['failure'],sort_keys=True)))
    return keys


def classify(snapshot,seen=()):
    current=failure_keys(snapshot)
    return {'new_failures':sorted(current-set(map(tuple,seen))),
            'seen_failures':sorted(current|set(map(tuple,seen))),
            'keep_observing':not (snapshot.get('final_seal_verified') is True and snapshot.get('temporary_removal_confirmed') is True),
            'terminal_state':'verified' if snapshot.get('final_seal_verified') is True else 'unknown'}


def controller_health(provider, process, serial):
    """Keep current observations distinct from retained serial history."""
    import re
    oom = bool(re.search(r'Out of memory: Killed process \d+ \(python[^)]*\)', serial))
    denied_at=accepted_at=-1
    for index,line in enumerate(serial.splitlines()):
        if 'logging.logEntries.create' in line and re.search(r'\b(denied|lacks|PERMISSION_DENIED)\b',line,re.I):
            denied_at=index
        try:
            record=json.loads(line)
            if isinstance(record,dict) and record.get('logging_write_accepted') is True and record.get('verified') is True:
                accepted_at=index
        except (ValueError,TypeError):
            pass
    logging_state=('resolved-in-retained-history' if accepted_at>denied_at>=0 else
                   'failure-in-retained-history' if denied_at>=0 else
                   'accepted-in-retained-history' if accepted_at>=0 else 'unknown')
    running = (process or {}).get('Running')
    cause = 'current process liveness not established'
    if running is True:
        cause = 'historical OOM evidence; current process running' if oom else 'container running'
    elif running is False:
        cause = 'container OOM kill' if process.get('OOMKilled') else 'container stopped; exact cause unknown'
    return {'provider_state': (provider or {}).get('status', 'unknown'),
            'process_state': 'running' if running is True else 'stopped' if running is False else 'unknown',
            'container_exit_code': (process or {}).get('ExitCode'),
            'serial_python_oom': oom, 'logging_permission_denied': denied_at>=0, 'logging_preflight_state': logging_state,
            'cause': cause, 'serial_scope': 'retained history; not proof of current process state'}
