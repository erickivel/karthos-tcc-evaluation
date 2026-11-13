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
