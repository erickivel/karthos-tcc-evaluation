"""Read-only statvfs acquisition of explicitly owned mount/device pairs."""
import os
import time


def acquire_owned(mounts, *, node, statvfs=os.statvfs, clock=time.time):
    if any(not m.get('owned') or not m.get('mount', '').startswith('/') or not m.get('device') for m in mounts):
        raise ValueError('explicit ownership, absolute mount and device required')
    if len({(m['mount'],m['device']) for m in mounts}) != len(mounts):
        raise ValueError('ambiguous duplicate mounts')
    rows=[]
    for mount in mounts:
        start=clock(); s=statvfs(mount['mount']); end=clock()
        for metric, value in (('capacity',s.f_blocks*s.f_frsize), ('free',s.f_bfree*s.f_frsize),
                              ('available',s.f_bavail*s.f_frsize), ('used',(s.f_blocks-s.f_bfree)*s.f_frsize)):
            rows.append(dict(node=node, metric='filesystem.'+metric, value=value, unit='bytes',
                             dimensions={'mount':mount['mount'], 'device':mount['device']},
                             source='statvfs', measurement_timestamp=None,
                             acquisition_started_at=start, acquisition_ended_at=end))
    return rows


def compare_static(reference, observed, *, absolute_tolerance=0):
    if not reference or not observed or any(reference.get(k)!=observed.get(k) for k in ('node','metric','unit','dimensions')):
        return {'state':'insufficient','reason':'missing or unmatched node/device/mount/metric/unit'}
    if not all(reference.get('dimensions',{}).get(k) for k in ('mount','device')):
        return {'state':'insufficient','reason':'missing mount/device identity'}
    if reference.get('value') is None or observed.get('value') is None:
        return {'state':'insufficient','reason':'missing value'}
    difference=observed['value']-reference['value']
    return {'state':'match' if abs(difference)<=absolute_tolerance else 'different', 'difference':difference}
