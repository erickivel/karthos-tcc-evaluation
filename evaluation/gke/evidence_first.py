"""Evidence-first orchestration. Analysis failure never requests more traffic."""
from __future__ import annotations
import os
import signal
import subprocess
import time
from pathlib import Path
from .models import write

MEMORY_BYTES = 6 * 1024**3


class AnalysisSafetyError(RuntimeError):
    """Analysis isolation could not be restored; stop new traffic."""


def enabled(ctx):
    return ctx.config.get('evidence_pipeline') == 'sealed-before-analysis-v1'


def after_export(receipt, scorer):
    if receipt.get('verified') is not True:
        raise ValueError('verified raw evidence required before scoring')
    try:
        report = scorer()
        report['analysis'] = {'state': 'complete'}
    except AnalysisSafetyError:
        raise
    except Exception as exc:
        report = {'execution': {'state': 'complete'}, 'evidence': {'valid': False},
                  'numerical_agreement': {'state': 'insufficient'},
                  'analysis': {'state': 'failed', 'type': type(exc).__name__, 'reason': str(exc)}}
    from .report_summary import SCOPE
    report.setdefault('schema_version',1)
    report.setdefault('report_scope',SCOPE)
    report['raw_evidence_export'] = receipt
    report['retry_traffic'] = False
    return report


def run_process(command, directory, *, timeout):
    """Bound lifetime and join the entire child process group before returning."""
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    started = time.time(); proc = None
    result = {'state': 'failed', 'started_at': started, 'workers_stopped': True,
              'memory_limit_bytes': MEMORY_BYTES, 'limit_semantics': 'Linux RLIMIT_AS; virtual address space'}
    try:
        with (directory/'worker.stdout').open('wb') as out, (directory/'worker.stderr').open('wb') as err:
            proc = subprocess.Popen(command, stdout=out, stderr=err, start_new_session=True)
            result['pid'] = proc.pid
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                result['reason'] = 'analysis deadline exceeded'
            finally:
                # A child may have descendants even after its own exit.
                result['workers_stopped']=False
                try: os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                proc.wait()
                until=time.monotonic()+5
                while True:
                    try:os.killpg(proc.pid,0)
                    except ProcessLookupError:
                        result['workers_stopped']=True;break
                    if time.monotonic()>=until:
                        result['reason']='analysis process group termination not verified';break
                    time.sleep(.05)
            result['returncode'] = proc.returncode
            if proc.returncode == 0 and result['workers_stopped'] and 'reason' not in result: result['state'] = 'complete'
            else: result.setdefault('reason', f'analysis process exited {proc.returncode}; cause requires diagnostics')
    except OSError as exc:
        result['reason'] = str(exc)
    result['finished_at'] = time.time()
    write(directory/'worker-status.json', result)
    return result


def verify_inputs(path, receipt, *, projection_proof=None):
    """Verify the sealed input population, allowing new analysis files only."""
    if projection_proof is not None:
        from .prepare_scoring_replay import verify_projection_inputs
        return verify_projection_inputs(path,receipt,projection_proof)
    from .models import sha
    path=Path(path).resolve();seal=path/'GKE-SHA256SUMS'
    if receipt.get('verified') is not True or sha(seal)!=receipt.get('original_window_seal_sha256'):
        raise ValueError('raw input seal checksum differs from verified upload')
    declared=set()
    for line in seal.read_text().splitlines():
        expected,name=line.split('  ',1);member=path/name
        if name in declared or member.is_symlink() or not member.resolve().is_relative_to(path):
            raise ValueError('unsafe or duplicate raw input member')
        declared.add(name)
        if not member.is_file() or sha(member)!=expected:raise ValueError('raw input checksum mismatch: '+name)
    if not declared:raise ValueError('empty raw input seal')
    for member in path.rglob('*'):
        if member.is_file() and member!=seal and str(member.relative_to(path)) not in declared:
            if member.relative_to(path).parts[0]!='analysis' and member.relative_to(path).as_posix()!='orchestration-summary.json':raise ValueError('unsealed scoring input: '+str(member.relative_to(path)))
    return {'verified':True,'members':len(declared)}
