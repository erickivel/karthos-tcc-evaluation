"""Descriptive observations, deliberately separate from exact accuracy verdicts."""
from collections import defaultdict
import math

TRANSITION_LIMIT = 16


def lifecycle_observations(candidates):
    """Candidates already passed the evaluator's Pod/node/container identity checks.

    Outer acquisition brackets bound observations of changed reference state. They
    do not locate an event, prove only one transition, or timestamp collector cache.
    Receiver inputs are not independent API history and are excluded here.
    """
    rows = sorted((row for row in candidates if not row[0].get('receiver_collector_uid')),
                  key=lambda row: float(row[0]['acquisition_started_at']))
    transitions = []
    total = 0
    for before, after in zip(rows, rows[1:]):
        a, _, old = before
        b, _, new = after
        if float(a['acquisition_ended_at']) >= float(b['acquisition_started_at']) or old == new:
            continue
        total += 1
        if len(transitions) < TRANSITION_LIMIT:
            transitions.append({'from_value': old, 'to_value': new,
                                'observation_interval_seconds': [float(a['acquisition_started_at']), float(b['acquisition_ended_at'])]})
    return {'state': 'observed' if rows else 'insufficient', 'reference_observations': len(rows),
            'transitions': transitions, 'transition_observations': total,
            'omitted_transition_observations': total - len(transitions),
            'exact_alignment_claimed': False,
            'first_api_observation_detection_lag': {'state': 'unavailable', 'reason': 'no independently timestamped source transition and first matching API acquisition paired by identity'},
            'timestamp_semantics': 'outer independent acquisition brackets in Unix seconds; not event time or collector cache time',
            'scope': 'reference state-change observations only; no gauge accuracy or exhaustive transition claim'}


def summarize(checks):
    grouped = {'host': defaultdict(list), 'lifecycle': defaultdict(list)}
    for check in checks:
        name = check.get('observed', {}).get('metric', check.get('metric', ''))
        family = 'host' if name.startswith('system.') else 'lifecycle' if name.startswith(('k8s.container.', 'k8s.pod.status.', 'k8s.pod.phase')) else None
        if family:
            grouped[family][name].append(check)
    result = {}
    for family, metrics in grouped.items():
        rows = []
        for name, items in sorted(metrics.items()):
            observed = [item['observed'] for item in items if item.get('observed')]
            times = [row['recorded_timestamp'] for row in observed if type(row.get('recorded_timestamp')) in (int, float) and math.isfinite(row['recorded_timestamp'])]
            rows.append({'metric': name, 'observations': len(observed),
                         'collection_timestamp_range_seconds': [min(times), max(times)] if times else None,
                         'exact_comparison_counts': {state: sum(item.get('state') == state for item in items) for state in ('pass', 'fail', 'insufficient')},
                         'bounded_reference_comparisons': sum(bool(item.get('bounded_observation', {}).get('reference_observations')) for item in items)})
        result[family] = {'state': 'observed' if any(row['observations'] for row in rows) else 'insufficient',
                          'metrics': rows, 'exact_alignment_claimed': False,
                          'timestamp_semantics': 'OTLP collection timestamps, not underlying host read or cached Kubernetes object timestamps',
                          'scope': 'descriptive coverage only; dimensions and values remain in detailed source evidence; exact accuracy verdicts unchanged'}
    return result
