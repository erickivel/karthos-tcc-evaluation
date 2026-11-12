"""Per-launch mechanism gate. Results are separate from accuracy scoring."""
from __future__ import annotations

import contextlib
import math
import time

from .models import write

SECONDS = 10800
RESERVE = 1200
VERSION = 3


class UnsafeReadiness(RuntimeError):
    """Shared capture, ownership or deadline failed. No more probe traffic."""


def matrix():
    rows = []

    def add(demo, source, phase, scenario, generator='k6', seconds=60):
        cell = demo+'-'+source
        rows.append(dict(id=f'ready-{cell}-{phase}-{generator}-{scenario}', repeat=1,
                         cell=cell, demo=demo, source=source, phase=phase,
                         scenario=scenario, generator=generator, duration_seconds=seconds,
                         rate=2, execution='serial'))

    def traffic(demo, source, generators=('k6',)):
        for generator in generators:
            add(demo, source, 'smoke', 'matched', generator)

    traffic('astronomy', 'mesh')
    traffic('boutique', 'mesh')
    add('boutique', 'mesh', 'arbitration', 'both-sources')
    for scenario in ('checkout', 'gateway'):
        add('boutique', 'mesh', 'restart', scenario)
    add('boutique', 'trace', 'arbitration', 'replanned-trace')
    traffic('boutique', 'trace')
    for scenario in ('network', 'cpu-memory', 'storage'):
        add('boutique', 'trace', 'resource', scenario, seconds=300 if scenario=='network' else 60)
    for scenario in ('checkout', 'gateway'):
        add('boutique', 'trace', 'restart', scenario)
    traffic('astronomy', 'trace')
    return rows


def definition():
    return {'version':VERSION, 'seconds':SECONDS, 'reserve_seconds':RESERVE, 'matrix':matrix()}


def validate_launch(ctx):
    from .models import THESIS, build_matrix
    if ctx.manifest.get('schedule') != THESIS:
        raise ValueError('new launches require the active k6-only thesis protocol; legacy schedules are analysis-only')
    if ctx.manifest.get('schedule') == 'thesis-qualification-v4':
        from .recoverable_run import validate_launch as validate_v4
        return validate_v4(ctx)
    expected = build_matrix(ctx.manifest['protocol_definition'], schedule=THESIS)
    if ctx.manifest.get('matrix') != expected:
        raise ValueError('active launch requires the exact k6-only measured matrix')
    if ctx.manifest.get('readiness_check') != definition():
        raise ValueError('mandatory readiness definition missing or changed; prepare a new launch')
    from .benchmark import validated_receipt
    validated_receipt(ctx.manifest.get('readiness_benchmark'))
    from .models import THESIS
    if ctx.manifest.get('schedule')==THESIS:
        from .thesis import local_contract
        local_contract(ctx.manifest.get('local_contract_evidence'),ctx.manifest['product_pins'])
    validate_cost(ctx)


def cutoff(ctx):
    return min(ctx.deadline, ctx.state['started_at'] + SECONDS)


def validate_cost(ctx):
    limits = ctx.config['limits']
    estimate = ctx.manifest['cost_preview']['gross_estimate']
    if (type(estimate) not in (int, float) or not math.isfinite(estimate)
            or not 0 <= estimate <= limits['launch_ceiling_usd'] <= limits['gross_limit_usd']):
        raise ValueError('combined cost does not satisfy the launch limits')
    return estimate


def forecast(ctx, records):
    slowest = {}
    archives = {}
    for row in records:
        duration = row['duration_seconds']
        estimate = row.get('forecast_seconds')
        historical = row.get('archived_total_seconds')
        if type(estimate) in (int, float) and math.isfinite(estimate) and estimate >= duration:
            slowest[duration] = max(estimate, slowest.get(duration, 0))
        if type(historical) in (int, float) and math.isfinite(historical) and historical >= duration:
            archives[duration] = max(historical, archives.get(duration, 0))
    setup = max((row.get('setup_seconds', 0) for row in records), default=0)
    total = 0
    previous = None
    for row in ctx.manifest['matrix']:
        duration = row['duration_seconds']
        if duration not in slowest:
            raise ValueError('qualification wall-time estimate is unavailable')
        key = (row.get('repeat'), row.get('cell'))
        new_setup = key != previous or row.get('phase') in ('arbitration', 'restart')
        # Archived totals already include deployment. Compare them with the
        # independently timed live phases before taking the conservative bound.
        total += max(slowest[duration] + (setup if new_setup else 0), archives.get(duration, 0))
        previous = key
    rebuild = 0
    if any(row.get('repeat')==2 for row in ctx.manifest['matrix']):
        rebuild = max((row.get('preflight_seconds', 0) for row in records), default=0)
        if rebuild <= 0:
            raise ValueError('cluster rebuild estimate unavailable')
    from .models import THESIS,THESIS_HISTORY_SECONDS
    history=THESIS_HISTORY_SECONDS if ctx.manifest.get('schedule')==THESIS else 0
    return total + rebuild + history + ctx.config['limits']['cleanup_reserve_seconds']


def execute(ctx, check, reset, publish, clock=time.time, finish=lambda: None, commit=lambda summary: None, initial_failure=None):
    root = ctx.result/'readiness'
    report_path = root/'matrix'
    report_path.mkdir(parents=True, exist_ok=False)
    records = []
    terminal = initial_failure
    arbitration = None
    try:
        validate_cost(ctx)
        for spec in matrix():
            row = dict(spec)
            if terminal or clock() + RESERVE + spec['duration_seconds'] >= cutoff(ctx):
                terminal = terminal or 'readiness deadline or cleanup reserve reached'
                row.update(state='unattempted', reason=terminal)
            elif spec['scenario']=='replanned-trace' and arbitration is None:
                row.update(state='blocked by dependency', reason='verified Istio arbitration required')
            else:
                try:
                    verdict = check(spec)
                    if verdict.get('state') not in ('passed', 'failed', 'blocked by dependency'):
                        raise ValueError('invalid readiness verdict')
                    row.update(verdict)
                    if row['state']=='passed' and row.get('export', {}).get('verified') is not True:
                        raise UnsafeReadiness('required probe receipt readback missing')
                    if verdict.get('unsafe'):
                        terminal = row.get('reason') or 'shared readiness failure'
                except UnsafeReadiness as exc:
                    terminal = str(exc)
                    row.update(state='failed', reason=terminal)
                except Exception as exc:
                    row.update(state='failed', reason=str(exc))
            if spec['scenario']=='both-sources' and row['state']=='passed':
                arbitration = spec['id']
            records.append(row)
    except (Exception, KeyboardInterrupt) as exc:
        terminal = str(exc) or type(exc).__name__
        done = {row['id'] for row in records}
        records.extend(dict(row, state='unattempted', reason=terminal) for row in matrix() if row['id'] not in done)
    finally:
        try:
            finish()
        except Exception as exc:
            terminal = terminal or str(exc)

    counts = {state:sum(row['state']==state for row in records)
              for state in ('passed','failed','blocked by dependency','unattempted')}
    summary = {'run_id': ctx.run_id, 'cases': records, 'counts':counts,
               'probe_state':'passed' if counts['passed']==len(matrix()) and not terminal else 'incomplete', 'ready': False,
               'case_deadline': cutoff(ctx)-RESERVE, 'error': terminal,
               'accuracy_scored': False}
    ctx.write(report_path/'matrix.json', summary)
    try:
        receipt = publish(report_path)
        if receipt.get('verified') is not True:
            raise ValueError('readiness matrix export did not verify')
        summary['matrix_export'] = receipt
        if terminal or any(row['state']!='passed' for row in records):
            raise ValueError(terminal or 'all 13 current-launch probes must pass')
        if clock()+RESERVE >= cutoff(ctx):
            raise TimeoutError('readiness reset budget exhausted')
        summary['reset'] = reset()
        if summary['reset'].get('valid') is not True:
            raise ValueError('readiness reset did not verify')
        if clock()+RESERVE >= cutoff(ctx):
            raise TimeoutError('readiness exceeded its reset deadline')
        readiness_only = ctx.config.get('execution_scope') == 'readiness-only'
        summary['execution_scope'] = 'readiness-only' if readiness_only else 'qualification'
        summary['forecast_seconds'] = ctx.config['limits']['cleanup_reserve_seconds'] if readiness_only else forecast(ctx, records)
        latest = min(cutoff(ctx)-RESERVE, ctx.deadline-summary['forecast_seconds'])
        if clock() >= latest:
            raise TimeoutError('frozen matrix and cleanup do not fit in remaining infrastructure time')
        summary['commit_before'] = latest
        commit(summary)
        summary['ready'] = True
    except (Exception, KeyboardInterrupt) as exc:
        summary['error'] = str(exc) or type(exc).__name__
    ctx.write(root/'handoff.json', summary)
    return summary


@contextlib.contextmanager
def budget(ctx):
    from .. import runner
    saved = runner._DEADLINE
    ctx.phase_deadline = cutoff(ctx)
    ctx.phase_reserve = RESERVE
    runner._DEADLINE = time.monotonic() + max(0, ctx.phase_deadline-time.time()-RESERVE)
    try:
        yield
    finally:
        runner._DEADLINE = saved
        del ctx.phase_deadline
        del ctx.phase_reserve
