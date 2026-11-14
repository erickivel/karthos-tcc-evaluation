"""Recorded evidence bounds, independent of fixed scientific query windows."""
from datetime import datetime, timezone
import json
import math
from pathlib import Path

VERSION = 'recorded-horizon-v1'


def epoch(value):
    if isinstance(value, bool): raise ValueError('boolean is not an evidence timestamp')
    try:
        stamp = float(value) if isinstance(value, (int, float)) else datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError('invalid evidence timestamp') from exc
    if not math.isfinite(stamp): raise ValueError('nonfinite evidence timestamp')
    return stamp


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _read(path):
    return json.loads(path.read_text()) if path.is_file() else {}


def resolve(path, start, end, preroll_seconds=60):
    path = Path(path); start = epoch(start); end = epoch(end)
    if start > end: raise ValueError('workload boundaries are reversed')
    manifest = _read(path/'manifest.json'); quiet = _read(path/'quiet-after.json')
    observations = dict(manifest.get('phase_boundaries', {}))
    if quiet.get('finished_at') is not None:
        observations['quiet_completed_at'] = quiet.get('completed_at_epoch', quiet['finished_at'])
    # Historical manifests may lack the explicit finalization timestamp.
    if not observations and manifest.get('finished_at') is not None:
        observations['legacy_attempt_finished_at'] = manifest['finished_at']
    recorded = {}
    for name, value in observations.items():
        if name not in ('drain_completed_at', 'quiet_completed_at', 'capture_finalized_at', 'legacy_attempt_finished_at'):
            continue
        stamp = epoch(value)
        if stamp < end: raise ValueError('evidence completion precedes workload end: '+name)
        recorded[name] = stamp
    return {'version': VERSION, 'state': 'recorded' if recorded else 'insufficient',
            'workload_start_epoch': start, 'workload_end_epoch': end,
            'export_start_epoch': start-preroll_seconds,
            'export_end_epoch': max([end, *recorded.values()]),
            'boundaries': recorded, 'quiet_stable': quiet.get('stable') is True,
            'semantics': 'recorded evidence horizon; workload and API query windows unchanged'}


def coverage(path, raw, bounds):
    """Inspect one exported series at a time; index only required quiet endpoints."""
    import io
    quiet = _read(Path(path)/'quiet-after.json')
    records = quiet.get('records', [])
    expected = records[-1].get('raw_rows', []) if records else []
    wanted = {}; checks = []
    for row in expected:
        metric=row['metric'];identity=json.dumps(metric,sort_keys=True,separators=(',', ':'))
        times,values=row.get('timestamps',[]),row.get('values',[])
        if len(times)!=len(values): raise ValueError('quiet timestamps/values differ in length')
        if not times: continue
        stamp=max(times);population=[value for at,value in zip(times,values) if at==stamp]
        if stamp/1000 < bounds['export_start_epoch'] or stamp/1000 > bounds['export_end_epoch']:
            checks.append({'metric':metric,'timestamp_ms':stamp,'state':'insufficient','reason':'quiet endpoint outside requested evidence horizon'})
            continue
        key=(identity,stamp)
        entry=wanted.setdefault(key,{'metric':metric,'timestamp_ms':stamp,'expected':[], 'seen':False,'conflict':False})
        entry['expected'].extend(population)
    lo=hi=None;series_count=0
    for line in io.StringIO(raw):
        if not line.strip(): continue
        row=json.loads(line);series_count+=1
        identity=json.dumps(row['metric'],sort_keys=True,separators=(',', ':'))
        times,values=row.get('timestamps',[]),row.get('values',[])
        if len(times)!=len(values): raise ValueError('VM export timestamps/values differ in length')
        for stamp,value in zip(times,values):
            lo=stamp if lo is None else min(lo,stamp);hi=stamp if hi is None else max(hi,stamp)
            entry=wanted.get((identity,stamp))
            if entry is not None:
                entry['seen']=True
                if any(value!=expected_value for expected_value in entry['expected']):entry['conflict']=True
    for entry in wanted.values():
        valid=entry['seen'] and not entry['conflict']
        checks.append({'metric':entry['metric'],'timestamp_ms':entry['timestamp_ms'],
            'state':'pass' if valid else 'insufficient',
            'reason':None if valid else 'quiet endpoint missing or conflicting in exported population'})
    passed=checks and all(c['state']=='pass' for c in checks) and quiet.get('stable') is True and bounds['state']=='recorded'
    return {'version':VERSION,'state':'pass' if passed else 'insufficient',
            'requested':bounds,'available_min_timestamp_ms':lo,'available_max_timestamp_ms':hi,
            'series_count':series_count,'quiet_endpoint_checks':checks,
            'semantics':'exact recorded quiet endpoints; no inference of packet loss or uniform per-series cadence'}
