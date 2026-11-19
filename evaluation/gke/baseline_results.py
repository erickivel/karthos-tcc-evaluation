"""Additive per-baseline claim evidence; original attempts are never hidden."""
from collections import Counter


def baseline_results(matrix, windows, exported, extract):
    baselines = []
    for spec in matrix:
        if spec.get('phase') != 'baseline':
            continue
        record = exported.get(spec['id'])
        uri, data = record if record else (None, {})
        verdict = extract(data) if record else 'insufficient'
        verdict = {'correct': 'pass', 'incorrect': 'fail', 'incomplete': 'insufficient'}.get(verdict, verdict)
        if verdict not in ('pass', 'fail', 'insufficient', 'not-applicable'):
            verdict = 'insufficient'
        observed_verdict=verdict
        if data.get('evidence',{}).get('valid') is False or data.get('execution',{}).get('state') not in (None,'complete'):
            verdict='insufficient'
        attempts = [{key: row[key] for key in ('id', 'attempt_id', 'state', 'execution', 'evidence_valid', 'export', 'reason', 'attempts', 'attempt_count') if key in row}
                    for row in windows if row.get('id') == spec['id'] or row.get('logical_id') == spec['id']]
        baselines.append(dict(id=spec['id'], cell=spec.get('cell'), repeat=spec.get('repeat'),
                              verdict=verdict, observed_verdict=observed_verdict, evidence_valid=data.get('evidence',{}).get('valid'), artifact=uri, attempts=attempts,
                              families=data.get('families', {})))
    states = Counter(row['verdict'] for row in baselines)
    verdict = 'fail' if states['fail'] else 'pass' if baselines and states['pass'] == len(baselines) else 'insufficient'
    return dict(verdict=verdict, baselines=baselines, counts=dict(
        planned=len(baselines), evaluable=states['pass']+states['fail'], passed=states['pass'],
        failed=states['fail'], insufficient=states['insufficient'], not_applicable=states['not-applicable']))
