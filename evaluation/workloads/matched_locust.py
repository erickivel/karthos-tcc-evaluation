"""Matched single-process Locust adapter; shared recipes live in workload.json."""
import itertools
import json
import os
import time
from datetime import datetime, timezone

from locust import HttpUser, constant_throughput, task, events
from common import journey, outcome_ok, window_record, trace_context
from locust_reports import install as install_reports

install_reports(events)

# One counter across all users: per-user task counters would distort the mix.
ITERATIONS = itertools.count()
RUN_ID = os.environ['RUN_ID']
DEMO = os.environ['DEMO']
WORKLOAD = os.environ.get('WORKLOAD', 'legacy')
USERS = 10
RATE = float(os.environ.get('RATE', '2'))


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


@events.request.add_listener
def native_request_reported(context=None, exception=None, **kwargs):
    marker=(context or {}).get('_evaluation_reported')
    if marker is not None:marker.update(value=True,failed=exception is not None)


WINDOW_START = None


@events.test_start.add_listener
def window_start(environment, **kwargs):
    global WINDOW_START
    WINDOW_START = timestamp()
    print('EVAL_WINDOW ' + json.dumps(window_record(RUN_ID, 'locust', WINDOW_START,
          None, int(os.environ.get('DURATION', '180s')[:-1]), RATE)), flush=True)


@events.test_stop.add_listener
def window_stop(environment, **kwargs):
    print('EVAL_WINDOW ' + json.dumps(window_record(RUN_ID, 'locust', WINDOW_START,
          timestamp(), int(os.environ.get('DURATION', '180s')[:-1]), RATE)), flush=True)


class MatchedUser(HttpUser):
    # Closed users, approximate target iterations/s when journeys fit their pace.
    # This is not k6's open arrival process, and overruns reduce achieved rate.
    wait_time = constant_throughput(RATE / USERS)

    @task
    def matched_journey(self):
        n = next(ITERATIONS)
        journey_id = f'{RUN_ID}:{n}'
        self.client.cookies.clear()
        for index, step in enumerate(journey(DEMO, WORKLOAD, n, RUN_ID)):
            request_id = f'{journey_id}:{index}'
            headers = dict(step['headers'])
            headers.update({'X-Evaluation-Run': RUN_ID, 'X-Evaluation-Request-ID': request_id})
            context = trace_context(request_id) if os.environ.get('EVAL_CORRELATE_TRACES') == '1' else {}
            if context: headers['traceparent'] = context['traceparent']
            body = step['body']
            if isinstance(body, dict):
                body = json.dumps(body, separators=(',', ':'))
            started_at = timestamp()
            print('EVAL_ATTEMPT ' + json.dumps(dict(request_id=request_id,
                  run_id=RUN_ID, generator='locust', route=step['route'],
                  journey_id=journey_id, started_at=started_at,trace_id=context.get('trace_id'))), flush=True)
            started = time.perf_counter()
            status = 0
            exception = None
            ok = False
            reported = dict(value=False,failed=False)
            try:
                with self.client.request(step['method'], step['path'], data=body,
                                         headers=headers, name=step['route'],
                                         context={"_evaluation_reported":reported},
                                         allow_redirects=False, timeout=15,
                                         catch_response=True) as response:
                    status = response.status_code
                    exception = str(response.error) if response.error else None
                    ok = outcome_ok(step, status, response.text)
                    if ok:
                        response.success()
                    else:
                        response.failure(f'expected status {step["expected_status"]} and {step["outcome"]}; got {status}')
            except Exception as error:
                exception = str(error)
            finally:
                print('EVAL_REQUEST ' + json.dumps(dict(
                    request_id=request_id, run_id=RUN_ID, generator='locust', route=step['route'],
                    journey_id=journey_id, iteration=n, step_index=index,trace_id=context.get('trace_id'),
                    started_at=started_at, completed_at=timestamp(),
                    duration_ms=(time.perf_counter()-started)*1000, status=status,
                    exception=exception, native_reported=reported["value"], native_failure=reported["failed"], expected_status=step['expected_status'], outcome_ok=ok,
                    attempted=True, completed=status > 0,
                ), separators=(',', ':')), flush=True)
