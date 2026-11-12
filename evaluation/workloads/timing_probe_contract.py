"""Evaluation-only fixed timing plan; recipes remain owned by workload.json."""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode

DURATION_SECONDS = 120
INNER_WINDOW = (30, 90)
OFFSETS = (20, 29, 31, 44, 46, 59, 61, 74, 76, 89, 91, 100)
REQUEST_TIMEOUT_SECONDS = 10
CONFIG = json.loads(Path(__file__).with_name('workload.json').read_text())


def expand(value, user):
    if isinstance(value, str): return value.replace('{user}', user)
    if isinstance(value, list): return [expand(v,user) for v in value]
    if isinstance(value, dict): return {k:expand(v,user) for k,v in value.items()}
    return value


def build_plan(demo, run_id):
    contract = CONFIG['demos'][demo]
    prefix = 'eval-' + hashlib.sha256(run_id.encode()).hexdigest()[:12]
    plan = []
    for offset in OFFSETS:
        for recipe in ('checkout', 'expired'):
            n = len(plan)
            user = f'{prefix}-{n}'
            journey_id = f'{prefix}:probe:{n}'
            steps = expand(contract['journeys'][recipe],user)
            for index,step in enumerate(steps):
                step.update(headers=expand(contract['headers'],user),
                            phase='checkout' if index==len(steps)-1 else 'prerequisite',
                            planned_offset_seconds=offset,recipe=recipe,
                            journey_id=journey_id,iteration=n,step_index=index,
                            request_id=f'{journey_id}:{index}')
                if step['body'] is not None:
                    form=contract['encoding']=='form'
                    step['headers']['Content-Type']='application/x-www-form-urlencoded' if form else 'application/json'
                    step['body']=urlencode(step['body']) if form else json.dumps(step['body'],separators=(',',':'))
            plan.append(dict(recipe=recipe,planned_offset_seconds=offset,steps=steps))
    return plan


def wait_seconds(origin, offset, now):
    return max(0, origin+offset-now)


def request_timeout(origin, now):
    return max(0,min(REQUEST_TIMEOUT_SECONDS,origin+DURATION_SECONDS-now))


def window_record(run_id, generator, started_at, ended_at=None):
    return dict(run_id=run_id,generator=generator,started_at=started_at,ended_at=ended_at,
                intended_duration_seconds=DURATION_SECONDS,target_iterations_per_second=24/120,
                expected_iterations=24,expected_checkouts=24,expected_probe_checkouts=24,expected_requests=72,
                offered_model='absolute-probe-schedule',probe_protocol_version=1,probe_schedule_offsets_seconds=list(OFFSETS),
                probe_mode='timing-probe',inner_start_offset_seconds=30,
                inner_end_offset_seconds=90,planned_offsets_seconds=list(OFFSETS))


def request_record(step,run_id,generator,started_at,completed_at,actual_offset_seconds,
                   duration_ms,status,exception,ok,context):
    return dict(request_id=step['request_id'],run_id=run_id,generator=generator,
                route=step['route'],journey_id=step['journey_id'],iteration=step['iteration'],
                step_index=step['step_index'],phase=step['phase'],recipe=step['recipe'],
                planned_offset_seconds=step['planned_offset_seconds'],actual_offset_seconds=actual_offset_seconds,
                trace_id=context.get('trace_id'),requested_trace_id=context.get('trace_id'),
                actual_trace_id=context.get('trace_id'),requested_traceparent=context.get('traceparent'),
                actual_traceparent=context.get('traceparent'),started_at=started_at,completed_at=completed_at,
                duration_ms=duration_ms,status=status,exception=exception,expected_status=step['expected_status'],
                outcome_ok=ok,attempted=True,completed=status>0)
