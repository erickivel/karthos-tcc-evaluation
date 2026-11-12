"""Run one timing campaign with a local evidence reserve; no automatic retries."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import time
from datetime import datetime,timezone
from pathlib import Path
from . import runner as r
from .timing_pilot import safe_run_id


def main():
 p=argparse.ArgumentParser();p.add_argument('--run-id',required=True);args=p.parse_args();safe_run_id(args.run_id)
 out=r.OUT/args.run_id
 if out.exists():raise FileExistsError(out)
 monitor=r.OUT/(args.run_id+'-monitor');monitor.mkdir(parents=True,exist_ok=False)
 command=['python3','-P','-m','evaluation.timing_pilot','run','--run-id',args.run_id]
 env=dict(os.environ,PYTHONPATH=str(r.ROOT),PYTHONDONTWRITEBYTECODE='1')
 metadata=dict(run_id=args.run_id,command=command,started_at=datetime.now(timezone.utc).isoformat(),
  local_disk_reserve_bytes=8*1024**3,live_budget_seconds=14400,artifact_recovery_seconds=1800,
  explicit_kubeconfig=os.environ.get('KUBECONFIG'),no_automatic_retries=True)
 r.write(monitor/'launch.json',metadata);stop_reason=None
 with (monitor/'controller.log').open('w') as log:
  process=subprocess.Popen(command,cwd=r.ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
  r.write(monitor/'controller-pid.txt',str(process.pid)+'\n')
  try:
   while process.poll() is None:
    free=shutil.disk_usage(r.ROOT).free
    with (monitor/'local-disk.ndjson').open('a') as output:output.write(json.dumps(dict(at=datetime.now(timezone.utc).isoformat(),free_bytes=free))+'\n')
    if free<8*1024**3 and stop_reason is None:
     stop_reason='local evidence reserve below8GiB; bounded export/restoration requested';process.send_signal(signal.SIGINT)
    time.sleep(10)
  except BaseException:
   if process.poll() is None:process.send_signal(signal.SIGINT)
   raise
  finally:
   if process.poll() is not None:
    r.write(monitor/'completion.json',dict(returncode=process.returncode,stop_reason=stop_reason,finished_at=r.utc()))
    r.checksums(monitor)
 print(json.dumps(dict(returncode=process.returncode,stop_reason=stop_reason,run=str(out))))
 raise SystemExit(process.returncode)
if __name__=='__main__':main()
