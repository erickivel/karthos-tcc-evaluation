"""Credential-free source sampler; NDJSON only, actual read-only measurements."""
import argparse
import json
import os
from pathlib import Path
import signal
import sys
import time

# Copied into helper image at this fixed layout; no cluster/cloud SDK imports.
ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from evaluation.references import host, filesystem


def sample_once(*,node,node_uid,pod_uid,run_id,host_root='/hostfs',owned_mounts=(),
                volume_only=False,read_text=None,statvfs=os.statvfs,stat=os.stat,clock=time.time):
    """Read host allowlist and exact attested owned mounts, preserving source units."""
    if not node or not node_uid or not pod_uid or not run_id:
        return [{'state':'insufficient','reason':'node/node UID/observer pod UID/run identity missing',
                 'node':node,'node_uid':node_uid,'observer_pod_uid':pod_uid,'run_id':run_id,'observed_at':clock()}]
    reader=read_text or (lambda path:Path(path).read_text())
    rows=[]
    if not volume_only:
        def host_read(path): return reader(str(Path(host_root)/path.lstrip('/')))
        rows.extend(host.acquire_proc(node=node,read_text=host_read,clock=clock))
    mounts=[]
    for item in owned_mounts:
        if item.get('owned') is not True or not item.get('mount','').startswith('/'):
            raise ValueError('explicitly owned absolute mounts required')
        device=stat(item['mount']).st_dev
        actual=str(os.major(device))+':'+str(os.minor(device))
        if item.get('device') is not None and item['device']!=actual:
            raise ValueError('owned mount device changed')
        mounts.append(dict(item,device=actual))
    if mounts: rows.extend(filesystem.acquire_owned(mounts,node=node,statvfs=statvfs,clock=clock))
    for row in rows:
        row.update(node_uid=node_uid,observer_pod_uid=pod_uid,run_id=run_id,scope='node' if row['source']=='linux-proc' else 'owned-volume')
    return rows


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--period',type=float,default=1)
    parser.add_argument('--duration',type=float,default=600)
    parser.add_argument('--volume-only',action='store_true')
    args=parser.parse_args(argv)
    if not .1<=args.period<=60 or not 1<=args.duration<=3600: parser.error('bounded period .1..60 and duration 1..3600 required')
    node=os.environ.get('NODE_NAME','');uids=json.loads(os.environ.get('NODE_UID_MAP','{}'))
    kwargs={'node':node,'node_uid':os.environ.get('NODE_UID') or uids.get(node),
            'pod_uid':os.environ.get('POD_UID',''),'run_id':os.environ.get('RUN_ID',''),
            'owned_mounts':json.loads(os.environ.get('OWNED_MOUNTS','[]')),'volume_only':args.volume_only}
    stop=False
    def halt(*_):
        nonlocal stop
        stop=True
    signal.signal(signal.SIGTERM,halt);signal.signal(signal.SIGINT,halt)
    deadline=time.monotonic()+args.duration
    while not stop and time.monotonic()<deadline:
        started=time.monotonic()
        try: rows=sample_once(**kwargs)
        except Exception as exc: rows=[{'state':'insufficient','error':str(exc),'observed_at':time.time(),
                                      'node':node,'node_uid':kwargs['node_uid'],'observer_pod_uid':kwargs['pod_uid'],'run_id':kwargs['run_id']}]
        for row in rows: print(json.dumps(row,sort_keys=True),flush=True)
        remaining=min(args.period-(time.monotonic()-started),deadline-time.monotonic())
        if remaining>0: time.sleep(remaining)
    return 0


if __name__=='__main__': raise SystemExit(main())
