"""Small finalized failure records; never copy a live capture to a summary."""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import tempfile

from .export import evidence_files, redact_text, export_projection, pack_window_archives, verify_archive_privacy
from .models import sha

JSON_LIMIT = 8 * 1024**2
REPORT_LIMIT = 64 * 1024**2
DIAGNOSTIC_LIMIT = 256 * 1024**2 + 65536
NAMES = ('gke-evaluation.json', 'orchestration-summary.json', 'transition.json', 'manifest.json',
         'generator-artifact-status.json', 'structured/status.json')
IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,220}')


def _attempts(records):
    found = {}
    for row in records:
        for attempt in row.get('attempts', []) or [row]:
            name = attempt.get('attempt_id')
            if isinstance(name, str) and IDENTIFIER.fullmatch(name):
                found[name] = attempt
    return found


def _error(exc):
    return {'type':type(exc).__name__, 'message':redact_text(str(exc))[:2000]}


def _json(path):
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError('symlink evidence refused')
    if path.stat().st_size > JSON_LIMIT:
        raise ValueError('JSON size bound exceeded')
    payload = json.loads(path.read_bytes())
    if not isinstance(payload, dict):
        raise ValueError('JSON object required')
    return payload


def _write(ctx, path, value, errors):
    try:
        ctx.write(path, value)
        return True
    except Exception as exc:
        errors.append({'path':str(path), 'error':_error(exc)})
        return False


def verified_report_reference(receipt, filename, path):
    """Only reuse content bound to a successful immutable archive publication.

    Old receipts lack these descriptors and keep the previous copy behavior.
    A later changed controller file is retained separately, never attributed to
    an earlier archive. Absence locally is acceptable only with a bound member.
    """
    if not isinstance(receipt,dict) or receipt.get('verified') is not True:
        return None
    if not re.fullmatch(r'gs://[a-z0-9][a-z0-9._-]+/[^\s]+/',str(receipt.get('uri',''))):
        return None
    objects=receipt.get('objects',{});members=receipt.get('retained_members',{})
    if (not isinstance(objects,dict) or 'window-index.json' not in objects
            or not any(re.fullmatch(r'window(?:-\d{3})?\.tar\.gz',name) for name in objects)):
        return None
    for record in [*objects.values(),members.get(filename,{})]:
        if (not isinstance(record,dict) or not re.fullmatch('[a-f0-9]{64}',str(record.get('sha256','')))
                or type(record.get('bytes')) is not int or record['bytes']<0):return None
    member=members[filename]
    if not re.fullmatch('[a-f0-9]{64}',str(member.get('source_sha256',''))):return None
    if path.is_symlink() or path.parent.is_symlink():return None
    if path.exists() and sha(path)!=member['source_sha256']:return None
    return member


def retain_attempt_reports(ctx, records, destination):
    """Retain allowlisted finalized JSON without changing source record paths.

    Main-thread report/transition files can be retained after a barrier failure.
    Capture-owned status requires independent worker/proxy termination proof.
    This does not export raw streams or claim that local validity was exported.
    """
    result = {'supplementary':True, 'attempts':[], 'errors':[], 'bytes':0}
    for name, attempt in _attempts(records).items():
        source = attempt.get('path')
        item = {'attempt_id':name, 'source_path':source, 'files':[]}
        result['attempts'].append(item)
        for filename in NAMES:
            row = {'name':filename, 'scope':'capture-completion' if filename=='structured/status.json' else 'finalized-controller-record'}
            item['files'].append(row)
            try:
                if not source:
                    raise ValueError('actual attempt path unavailable')
                if Path(source).is_symlink():
                    raise ValueError('symlink attempt directory refused')
                path = Path(source)/filename
                receipt=attempt.get('export',{})
                member=verified_report_reference(receipt,filename,path)
                if member is not None:
                    target=Path(destination)/'attempt-reports'/name/'export-reference.json'
                    if not _write(ctx,target,{'attempt_id':name,'export':receipt,
                            'scope':'finalized reports preserved in verified immutable window archives'},result['errors']):
                        raise ValueError('export reference could not be retained')
                    row.update(state='export-reference',member=member,reference=str(target.relative_to(destination)))
                    continue
                value = _json(path)
                if filename=='structured/status.json' and not (
                        value.get('workers_stopped') is True and value.get('proxy_stopped') is True):
                    raise ValueError('capture workers/proxies termination not verified; mutable capture excluded')
                size = path.stat().st_size
                if result['bytes']+size>REPORT_LIMIT:
                    raise ValueError('finalized report total size bound exceeded')
                target = Path(destination)/'attempt-reports'/name/filename
                target.parent.mkdir(parents=True, exist_ok=True)
                # Preserve original finalized bytes and their bounded size;
                # reserializing arbitrary Unicode can expand evidence greatly.
                shutil.copy2(path, target)
                result['bytes'] += size
                row.update(state='retained', path=str(target.relative_to(destination)))
            except Exception as exc:
                row.update(state='not_retained', error=_error(exc))
    _write(ctx, Path(destination)/'attempt-reports/index.json', result, result['errors'])
    return result


def copy_summary_diagnostics(ctx, records, summary):
    """Avoid re-uploading exported logs; preserve only stopped fatal diagnostics."""
    result = {'supplementary':True, 'attempts':[], 'errors':[]}
    attempts = _attempts(records)
    root = Path(ctx.result)/'attempt-diagnostics'
    try:
        sources = {path.name:path for path in root.iterdir() if path.is_dir()}
    except FileNotFoundError:
        sources = {}
    except Exception as exc:
        result['errors'].append({'operation':'list-diagnostics', 'error':_error(exc)})
        sources = {}
    for name, source in sorted(sources.items()):
        if not IDENTIFIER.fullmatch(name):
            result['errors'].append({'operation':'diagnostic-identity', 'name':name, 'error':'invalid identity'})
            continue
        attempt = attempts.get(name, {})
        item = {'attempt_id':name, 'source_path':str(source)}
        result['attempts'].append(item)
        target = Path(summary)/'attempt-diagnostics'/name
        receipt = attempt.get('export') or {}
        try:
            status = _json(source/'diagnostics-status.json')
            if status.get('stopped') is not True:
                raise ValueError('diagnostic writer termination not verified')
            if receipt.get('verified') is True:
                if not _write(ctx, target/'diagnostics-status.json', status, result['errors']):
                    raise ValueError('diagnostic status write failed')
                if not _write(ctx, target/'export-reference.json', {
                        'attempt_id':name, 'export':receipt,
                        'scope':'diagnostic logs already included in verified window archive; not duplicated'}, result['errors']):
                    raise ValueError('diagnostic export reference write failed')
                item.update(state='export-reference-only', export=receipt)
            else:
                if source.is_symlink():
                    raise ValueError('symlink diagnostic directory refused')
                source = source.resolve()
                files = evidence_files(source)
                size = sum(path.stat().st_size for path in files)
                if size>DIAGNOSTIC_LIMIT:
                    raise ValueError('fatal diagnostic directory exceeds bounded collector allowance')
                # Project once before archiving; no uninspected original or loose
                # log copy reaches a normal summary. The same archive/privacy
                # bounds as window publication apply to these supplemental logs.
                with tempfile.TemporaryDirectory(prefix='gke-diagnostic-projection-') as temporary:
                    projection = Path(temporary).resolve()/'evidence'
                    export_projection(source, projection)
                    members = [{'name':str(path.relative_to(projection)), 'sha256':sha(path),
                                'bytes':path.stat().st_size} for path in evidence_files(projection)]
                    gaps = []
                    log_index = projection/'log-index.ndjson'
                    if log_index.exists():
                        for line in log_index.read_text().splitlines():
                            row = json.loads(line)
                            if row.get('coverage_gap'):
                                gaps.append(row)
                    staged = Path(temporary)/'archives'
                    staged.mkdir()
                    parts = pack_window_archives(projection, staged)
                    for part in parts:
                        verify_archive_privacy(staged/part['name'])
                    if not _write(ctx, staged/'diagnostic-archive-index.json', {
                            'schema_version':1, 'supplementary':True, 'parts':parts,
                            'members':members, 'coverage_gaps':gaps,
                            'scope':'stopped diagnostics only; no scientific validity claim'}, result['errors']):
                        raise ValueError('diagnostic archive index write failed')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(staged, target)
                item.update(state='retained-stopped-fatal-diagnostics', files=len(files), bytes=size,
                            archive_index=str((target/'diagnostic-archive-index.json').relative_to(summary)))
        except Exception as exc:
            item.update(state='not_retained', error=_error(exc))
            _write(ctx, Path(summary)/'attempt-diagnostics'/(name+'-unavailable.json'), {
                'attempt_id':name, 'state':'not_retained', 'reason':_error(exc),
                'scope':'mutable, missing, corrupt or oversized diagnostics excluded',
                'export':receipt if receipt.get('verified') is True else None}, result['errors'])
    _write(ctx, Path(summary)/'attempt-diagnostics/index.json', result, result['errors'])
    return result


def retain_setup_evidence(ctx,driver,spec,destination):
    """Snapshot only the current, exited setup inside stopped diagnostics.

    Normal failures copy this into their window; interrupted/fatal failures use
    the existing stopped-diagnostic archive path. Never borrow a prior setup.
    """
    destination=Path(destination)
    receipt={'attempt_id':spec['id'],'state':'not-current','files':[]}
    attempt=getattr(driver,'setup_attempt_id',None)
    source=getattr(driver,'setup_path',None)
    if attempt and attempt==spec['id'] and source is not None:
        source=Path(source)
        if source.is_symlink() or not source.resolve().is_relative_to(Path(ctx.result).resolve()):
            raise ValueError('setup evidence outside owned run')
        source=source.resolve()
        receipt['source_path']=str(source)
        if source.is_dir():
            files=evidence_files(source)
            existing=sum(p.stat().st_size for p in evidence_files(destination.parent)) if destination.parent.exists() else 0
            if existing+sum(p.stat().st_size for p in files)>DIAGNOSTIC_LIMIT:
                raise ValueError('setup and diagnostic evidence exceed existing attempt bound')
            for path in files:
                relative=path.relative_to(source);target=destination/relative;expected=sha(path)
                target.parent.mkdir(parents=True,exist_ok=True)
                if target.exists():
                    if sha(target)!=expected:raise ValueError('setup snapshot replacement refused')
                else:shutil.copy2(path,target)
                if sha(path)!=expected or sha(target)!=expected:raise ValueError('setup evidence changed during snapshot')
                receipt['files'].append({'name':str(relative),'sha256':expected,'bytes':target.stat().st_size})
            receipt['state']='retained' if files else 'empty'
        else:receipt['state']='unavailable'
    ctx.write(destination/'setup-retention.json',receipt)
    return receipt
