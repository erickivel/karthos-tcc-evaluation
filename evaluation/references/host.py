"""Linux proc source parsers; all conversions left explicit to comparison callers."""
import math
import time


def read_proc(texts, *, node, requested_at, received_at, boot_id=None):
    if received_at < requested_at:
        raise ValueError('inverted acquisition interval')
    rows = []
    base = dict(node=node, source='linux-proc', boot_id=boot_id,
                acquisition_started_at=requested_at, acquisition_ended_at=received_at,
                measurement_timestamp=None)

    def emit(metric, value, unit, **dimensions):
        rows.append(dict(base, metric=metric, value=int(value), unit=unit, dimensions=dimensions))

    states = ('user', 'nice', 'system', 'idle', 'iowait', 'irq', 'softirq', 'steal', 'guest', 'guest_nice')
    for line in texts.get('stat', '').splitlines():
        fields = line.split()
        if fields and (fields[0] == 'cpu' or fields[0][3:].isdigit() and fields[0].startswith('cpu')):
            for state, value in zip(states, fields[1:]):
                emit('cpu.time', value, 'ticks', cpu=fields[0], state=state)
    for line in texts.get('meminfo', '').splitlines():
        fields = line.split()
        if len(fields) >= 2:
            emit('memory.'+fields[0].rstrip(':'), fields[1], fields[2] if len(fields) > 2 else 'count')
    for line in texts.get('diskstats', '').splitlines():
        f = line.split()
        if len(f) >= 14:
            for index, direction in ((5, 'read'), (9, 'write')):
                emit('disk.sectors', f[index], 'sectors', device=f[2], major=f[0], minor=f[1], direction=direction)
    for line in texts.get('net/dev', '').splitlines():
        if ':' not in line:
            continue
        name, values = line.split(':', 1)
        f = values.split()
        if len(f) >= 16:
            for index, direction in ((0, 'receive'), (8, 'transmit')):
                emit('network.bytes', f[index], 'bytes', interface=name.strip(), direction=direction)
    return rows


def acquire_proc(*, node, read_text, clock=time.time):
    """Read only allowlisted host paths via injected local/remote read_text."""
    started = clock()
    texts = {name: read_text('/proc/'+name) for name in ('stat', 'meminfo', 'diskstats', 'net/dev')}
    boot_id = read_text('/proc/sys/kernel/random/boot_id').strip()
    return read_proc(texts, node=node, requested_at=started, received_at=clock(), boot_id=boot_id)


def counter_delta(before, after):
    """Conservative rate bounds from disjoint actual acquisition brackets.

    Values must represent one uniquely selected directional monotonic series;
    callers must not silently pick from duplicate matches.
    """
    def insufficient(reason):
        return {'state': 'insufficient', 'reason': reason}
    if not isinstance(before, dict) or not isinstance(after, dict) or not before or not after:
        return insufficient('missing or ambiguous observation')
    if any(row.get('measurement_timestamp') is not None for row in (before, after)):
        return insufficient('source-timestamped counters require source-time comparison; acquisition brackets are not measurement times')
    keys = ('node', 'metric', 'source', 'unit', 'dimensions', 'boot_id', 'pod_uid',
            'container', 'container_start_time', 'pod_start_time', 'node_start_time')
    if any(before.get(k) != after.get(k) for k in keys):
        return insufficient('series identity or lifecycle changed')
    if not before.get('boot_id') and not before.get('pod_uid'):
        return insufficient('lifecycle identity unavailable')
    if before.get('scope') == 'container' and not before.get('container_start_time'):
        return insufficient('container lifecycle unavailable')
    try:
        a, b = before['acquisition_started_at'], before['acquisition_ended_at']
        c, d = after['acquisition_started_at'], after['acquisition_ended_at']
        delta = after['value'] - before['value']
        if not all(math.isfinite(x) for x in (a,b,c,d,delta)) or a > b or c > d or c <= b:
            return insufficient('ambiguous or overlapping acquisition times')
        if delta < 0:
            return insufficient('counter reset')
        return {'state': 'observed', 'delta': delta, 'elapsed_bounds': [c-b, d-a],
                'rate_bounds': [delta/(d-a), delta/(c-b)], 'unit': before['unit']+'/s'}
    except (KeyError, TypeError, ValueError):
        return insufficient('missing or invalid value/time')
