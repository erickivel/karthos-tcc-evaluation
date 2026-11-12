"""Controller phase timings, independent of accuracy windows and sample clocks."""
from contextlib import contextmanager
import json
import resource
import time


def usage():
    own = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return own.ru_utime + own.ru_stime + children.ru_utime + children.ru_stime, own.ru_maxrss, children.ru_maxrss


@contextmanager
def timed(ctx, phase, case=None):
    if not hasattr(ctx, 'result'):
        yield {}
        return
    path = ctx.result/'phase-timings.ndjson'
    path.parent.mkdir(parents=True, exist_ok=True)
    began = time.monotonic()
    cpu, _, _ = usage()
    status = 'complete'
    record = {'phase':phase, 'case':case, 'started_at':time.time()}
    with path.open('a') as stream:
        stream.write(json.dumps(dict(record, state='running'))+'\n')
    try:
        yield record
    except BaseException as exc:
        status = 'failed'
        record['error_type'] = type(exc).__name__
        raise
    finally:
        now_cpu, own_peak, children_peak = usage()
        record.update(state=status, elapsed_seconds=time.monotonic()-began,
                      cpu_seconds=now_cpu-cpu, controller_peak_rss=own_peak,
                      child_peak_rss=children_peak, rss_units='KiB on Linux')
        with path.open('a') as stream:
            stream.write(json.dumps(record)+'\n')


def last_completed(path):
    if not path.is_file():
        return None
    last = None
    with path.open() as stream:
        for line in stream:
            record = json.loads(line)
            if record['state']=='complete':
                last = record
    return last
