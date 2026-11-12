"""Single-process Locust timing probe. Invoke with --users 1 --spawn-rate 1.

All carts are prepared before scheduling the 24 checkout greenlets. The same
monotonic origin controls prerequisite deadlines and absolute checkout offsets.
"""
import json
import os
import time
from datetime import datetime, timezone

import gevent
from locust import HttpUser, task, events
from locust.exception import StopUser
from common import outcome_ok, trace_context
from locust_reports import install as install_reports
from timing_probe_contract import build_plan, request_record, request_timeout, wait_seconds, window_record

install_reports(events)
RUN_ID = os.environ['RUN_ID']
DEMO = os.environ['DEMO']
ORIGIN = None
STARTED_AT = None
ORIGIN_EPOCH_MILLISECONDS = None
STARTED = False
COMPLETED = False


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')


def emit(kind,record):
    print(kind+' '+json.dumps(record,separators=(',',':')),flush=True)


@events.request.add_listener
def native_request_reported(context=None,exception=None,**kwargs):
    marker=(context or {}).get('_evaluation_reported')
    if marker is not None: marker.update(value=True,failed=exception is not None)


@events.test_start.add_listener
def start_window(environment,**kwargs):
    global ORIGIN, STARTED_AT, ORIGIN_EPOCH_MILLISECONDS
    ORIGIN=time.monotonic()
    ORIGIN_EPOCH_MILLISECONDS=time.time()*1000
    STARTED_AT=timestamp()
    marker=window_record(RUN_ID,'locust',STARTED_AT)
    marker.update(origin_epoch_milliseconds=ORIGIN_EPOCH_MILLISECONDS,origin_monotonic_seconds=ORIGIN)
    emit('EVAL_WINDOW',marker)


@events.test_stop.add_listener
def stop_window(environment,**kwargs):
    marker=window_record(RUN_ID,'locust',STARTED_AT,timestamp())
    marker.update(origin_epoch_milliseconds=ORIGIN_EPOCH_MILLISECONDS,origin_monotonic_seconds=ORIGIN)
    emit('EVAL_WINDOW',marker)
    if not COMPLETED:
        emit('EVAL_PROBE_COMPLETE',dict(run_id=RUN_ID,generator='locust',complete=False,
             reason='test stopped before probe completion',completed_at=timestamp()))


class TimingProbeUser(HttpUser):
    def request_step(self,step):
        # Last guard is immediately before issuing the actual HTTP request.
        now=time.monotonic()
        timeout=request_timeout(ORIGIN,now)
        if step['phase']=='prerequisite': timeout=min(timeout,max(0,ORIGIN+20-now))
        if timeout<=0:
            raise RuntimeError('probe request deadline reached')
        self.client.cookies.clear()
        headers=dict(step['headers'])
        headers.update({'X-Evaluation-Run':RUN_ID,'X-Evaluation-Request-ID':step['request_id']})
        context=trace_context(step['request_id']) if os.environ.get('EVAL_CORRELATE_TRACES')=='1' else {}
        if context: headers['traceparent']=context['traceparent']
        started_at=timestamp()
        started=time.monotonic()
        base=request_record(step,RUN_ID,'locust',started_at,None,started-ORIGIN,0,0,None,False,context)
        emit('EVAL_ATTEMPT',base)
        status=0
        exception=None
        ok=False
        reported=dict(value=False,failed=False)
        try:
            with self.client.request(step['method'],step['path'],data=step['body'],headers=headers,
                                     name=step['route'],context={'_evaluation_reported':reported},
                                     allow_redirects=False,timeout=timeout,catch_response=True) as response:
                status=response.status_code
                exception=str(response.error) if response.error else None
                ok=outcome_ok(step,status,response.text)
                if ok: response.success()
                else: response.failure(f'expected {step["expected_status"]} and {step["outcome"]}; got {status}')
        except Exception as error:
            exception=str(error)
        finally:
            record=request_record(step,RUN_ID,'locust',started_at,timestamp(),started-ORIGIN,
                      (time.monotonic()-started)*1000,status,exception,ok,context)
            record.update(native_reported=reported['value'],native_failure=reported['failed'])
            emit('EVAL_REQUEST',record)
        return ok

    def checkout_at(self,item):
        gevent.sleep(wait_seconds(ORIGIN,item['planned_offset_seconds'],time.monotonic()))
        self.request_step(item['steps'][-1])

    @task
    def probe(self):
        global STARTED, COMPLETED
        if STARTED: raise StopUser()
        STARTED=True
        complete=False
        reason=None
        try:
            plan=build_plan(DEMO,RUN_ID)
            # Separate client cookie jars are unnecessary: explicit recipe cookies
            # own identity and each request clears ambient response cookies.
            for item in plan:
                for step in item['steps'][:-1]:
                    if not self.request_step(step):
                        raise RuntimeError('prerequisite outcome failed')
            if time.monotonic()>=ORIGIN+20:
                raise RuntimeError('prerequisites missed first checkout offset')
            workers=[gevent.spawn(self.checkout_at,item) for item in plan]
            gevent.joinall(workers,raise_error=True)
            complete=True
        except Exception as error:
            reason=str(error)
        gevent.sleep(wait_seconds(ORIGIN,120,time.monotonic()))
        COMPLETED=True
        emit('EVAL_PROBE_COMPLETE',dict(run_id=RUN_ID,generator='locust',complete=complete,
             reason=reason,completed_at=timestamp(),actual_duration_seconds=time.monotonic()-ORIGIN))
        self.environment.runner.quit()
        raise StopUser()
