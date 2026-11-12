"""Pod-local evidence from the independent host sampler."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import subprocess
import time

SPOOL_BYTES = 256 * 1024**2
SPOOL_DIR = '/reference'
SPOOL_PATH = SPOOL_DIR+'/records.ndjson'

# The immutable helper still performs the same reads. This launcher redirects
# its output to an exclusive, size-limited file before replacing itself.
LAUNCHER = f"""import os,resource,sys
resource.setrlimit(resource.RLIMIT_FSIZE, ({SPOOL_BYTES}, {SPOOL_BYTES}))
fd=os.open({SPOOL_PATH!r}, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
os.dup2(fd,1)
os.close(fd)
os.execvp(sys.argv[1],sys.argv[1:])
"""

# Snapshot a bounded prefix of the open file. Retain only complete source
# lines, then independently verify the gzip and the hash on the controller.
EXPORT = """import gzip,hashlib,json,os,sys
limit=256*1024**2
with open(sys.argv[1],'rb') as source:
    size=os.fstat(source.fileno()).st_size
    if not 0<size<=limit: raise ValueError('reference spool byte bound')
    digest=hashlib.sha256()
    left=size
    tail=b''
    count=0
    with gzip.GzipFile(fileobj=sys.stdout.buffer,mode='wb',compresslevel=1,mtime=0) as out:
        while left:
            chunk=source.read(min(left,65536))
            if not chunk: raise ValueError('reference spool shortened during snapshot')
            left-=len(chunk)
            head,sep,tail=(tail+chunk).rpartition(b'\\n')
            if len(tail)>1024**2: raise ValueError('reference spool line exceeds byte bound')
            if sep:
                complete=head+sep
                out.write(complete)
                digest.update(complete)
                count+=len(complete)
sys.stdout.buffer.flush()
print(json.dumps({'complete_bytes':count,'partial_line_bytes':len(tail),'snapshot_bytes':size,'sha256':digest.hexdigest()}),file=sys.stderr,flush=True)
"""


def decode_snapshot(compressed, evidence):
    seal = json.loads(evidence)
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as archive:
        data = archive.read(SPOOL_BYTES+1)
    if (not 0<len(data)<=SPOOL_BYTES or not data.endswith(b'\n')
            or len(data)!=seal['complete_bytes']
            or not 0<=seal['partial_line_bytes']<=1024**2
            or seal['snapshot_bytes']!=len(data)+seal['partial_line_bytes']
            or seal['snapshot_bytes']>SPOOL_BYTES):
        raise ValueError('reference spool byte bounds or line framing disagree')
    if hashlib.sha256(data).hexdigest()!=seal['sha256']:
        raise ValueError('reference spool checksum mismatch')
    return data, seal


def read_spool(ctx, namespace, pod):
    attempts = []
    for number in (1,2):
        started = time.time_ns()
        try:
            response = ctx.kube('exec','-n',namespace,pod,'-c','reference','--',
                                'python3','-c',EXPORT,SPOOL_PATH,
                                timeout=120,binary=True,check=False)
            if response.returncode:
                raise RuntimeError('reference spool export failed: '+response.stderr.decode(errors='replace')[-500:])
            data, seal = decode_snapshot(response.stdout,response.stderr)
            return data, dict(seal, mode='bounded-pod-file', attempts=attempts,
                              export_started_at_ns=started, export_finished_at_ns=time.time_ns(),
                              compressed_sha256=hashlib.sha256(response.stdout).hexdigest())
        except (OSError, EOFError, KeyError, TypeError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            attempts.append({'number':number,'started_at_ns':started,'finished_at_ns':time.time_ns(),
                             'error':str(exc)})
    return None, {'mode':'bounded-pod-file','attempts':attempts,'error':'reference spool could not be verified'}
