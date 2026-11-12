"""Download immutable generator files and verify bytes plus completion semantics."""
from __future__ import annotations

import csv
import hashlib
import json
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

REMOTE = '/tmp/eval-artifacts'
CORE = {'generator.log','generator-version.txt','run-id.txt','workload-started-at.txt',
        'workload-ended-at.txt','workload-exit-code.txt'}
LOCUST = {'locust-stats.csv','locust-failures.csv','locust-exceptions.csv',
          'locust-stats_history.csv','locust-report.html'}
K6 = {'k6.ndjson','k6-summary.json'}


def checksum_manifest(text: str) -> dict[str,str]:
    result={}
    for line in text.splitlines():
        match=re.fullmatch(r'([0-9a-f]{64}) [ *]([A-Za-z0-9][A-Za-z0-9._-]*)',line)
        if not match or match[2] in result or match[2] not in CORE|LOCUST|K6:
            raise ValueError('invalid, duplicated or unsupported artifact manifest entry')
        result[match[2]]=match[1]
    if not result:raise ValueError('empty artifact checksum manifest')
    return result


def validate_export(path: Path, generator: str, mode: str, run_id: str) -> dict:
    from .pilot import extract_events
    path=Path(path)
    try:
        if mode=='idle-boundary' and generator!='k6':raise ValueError('idle-boundary is a k6-only diagnostic')
        manifest=checksum_manifest((path/'SHA256SUMS').read_text())
        required=CORE|(K6 if generator=='k6' else LOCUST)
        if not required<=manifest.keys():raise ValueError('missing required checksum entries: '+','.join(sorted(required-manifest.keys())))
        for name,digest in manifest.items():
            file=path/name
            if not file.is_file() or file.is_symlink() or hashlib.sha256(file.read_bytes()).hexdigest()!=digest:
                raise ValueError('missing or corrupt artifact: '+name)
        if (path/'run-id.txt').read_text().strip()!=run_id:raise ValueError('artifact run ID mismatch')
        code=int((path/'workload-exit-code.txt').read_text().strip())
        if not 0<=code<=255:raise ValueError('invalid workload exit code')
        start=(path/'workload-started-at.txt').read_text().strip()
        end=(path/'workload-ended-at.txt').read_text().strip()
        if datetime.fromisoformat(end.replace('Z','+00:00'))<datetime.fromisoformat(start.replace('Z','+00:00')):
            raise ValueError('inverted workload timestamps')
        if not (path/'generator-version.txt').stat().st_size:raise ValueError('missing generator version')
        log=(path/'generator.log').read_text()
        records=[]
        if mode in ('matched','native-ledger','idle-boundary','timing-probe'):
            markers=extract_events(log,'WINDOW');attempts=extract_events(log,'ATTEMPT');records=extract_events(log,'REQUEST')
            if len(markers)!=2 or not markers[-1].get('ended_at') or markers[0].get('ended_at'):
                raise ValueError('missing or ambiguous final generator window marker')
            if any(row.get('run_id')!=run_id for row in markers+attempts+records):raise ValueError('generator event run ID mismatch')
            if not records or not attempts:raise ValueError('missing generator ledger')
            ids=[x['request_id'] for x in records];attempt_ids=[x['request_id'] for x in attempts]
            if len(set(ids))!=len(ids) or len(set(attempt_ids))!=len(attempt_ids) or set(ids)!=set(attempt_ids):
                raise ValueError('generator attempt/terminal accounting mismatch')
            first=datetime.fromisoformat(markers[0]['started_at'].replace('Z','+00:00'))
            last=datetime.fromisoformat(markers[-1]['ended_at'].replace('Z','+00:00'))
            if first>last or markers[0]['started_at']!=markers[-1]['started_at']:
                raise ValueError('inconsistent generator marker bounds')
        if mode=='timing-probe':
            from .workloads.timing_probe_contract import OFFSETS
            if any(m.get('probe_mode')!='timing-probe' or m.get('offered_model')!='absolute-probe-schedule'
                   or m.get('expected_probe_checkouts')!=24 or m.get('intended_duration_seconds')!=120
                   or m.get('probe_schedule_offsets_seconds')!=list(OFFSETS) for m in markers):
                raise ValueError('invalid timing-probe window contract')
            checkouts=[row for row in records if row.get('phase')=='checkout']
            if len(checkouts)!=24 or len({(row.get('planned_offset_seconds'),row.get('recipe')) for row in checkouts})!=24:
                raise ValueError('timing-probe checkout accounting incomplete')
            expected={(offset,recipe) for offset in OFFSETS for recipe in ('checkout','expired')}
            if {(row.get('planned_offset_seconds'),row.get('recipe')) for row in checkouts}!=expected:
                raise ValueError('timing-probe checkout schedule mismatch')
            complete=extract_events(log,'PROBE_COMPLETE')
            if len(complete)!=1 or complete[0].get('run_id')!=run_id or not complete[0].get('complete'):
                raise ValueError('timing-probe completion evidence missing')
        native_totals={}
        if generator=='k6':
            summary=json.loads((path/'k6-summary.json').read_text())
            if mode in ('matched','idle-boundary','timing-probe') and summary['metrics']['http_reqs']['values']['count']!=len(records):
                raise ValueError('k6 summary and terminal ledger counts differ')
            granular=[json.loads(line) for line in (path/'k6.ndjson').read_text().splitlines() if line.strip()]
            dropped=summary['metrics'].get('dropped_iterations',{}).get('values',{}).get('count')
            if dropped is None and mode not in ('idle-boundary','timing-probe'):
                raise ValueError('missing k6 summary scheduling evidence')
            if dropped is not None and (not isinstance(dropped,(int,float)) or dropped<0):
                raise ValueError('invalid k6 summary scheduling evidence')
            granular_drops=sum(x['data']['value'] for x in granular
                              if x.get('type')=='Point' and x.get('metric')=='dropped_iterations')
            if (dropped is not None and granular_drops!=dropped) or (dropped is None and granular_drops):
                raise ValueError('k6 granular and summary dropped-work totals differ')
            if mode=='timing-probe' and summary['metrics'].get('probe_scheduling_drops',{}).get('values',{}).get('count')!=0:
                raise ValueError('timing-probe scheduling evidence missing or dropped')
            if mode=='idle-boundary':
                if any(m.get('mode')!='idle-boundary' or m.get('offered_model')!='closed-per-vu-iterations'
                       or m.get('expected_pairs')!=21 or m.get('expected_requests')!=42 or m.get('expected_iterations')!=21 for m in markers):
                    raise ValueError('invalid idle-boundary closed-mode marker')
                if len(records)!=42 or summary['metrics'].get('iterations',{}).get('values',{}).get('count')!=21:
                    raise ValueError('idle-boundary closed iterations did not complete21pairs/42requests')
                pairs={n:[] for n in range(21)}
                for row in records:
                    if row.get('pair_index') not in pairs:raise ValueError('invalid idle-boundary pair index')
                    pairs[row['pair_index']].append(row.get('phase'))
                if any(sorted(phases)!=['probe','warm'] for phases in pairs.values()):
                    raise ValueError('missing or duplicate idle-boundary pair phases')
            # A zero-drop run need not emit a dropped_iterations datapoint.
            requests=sum(x['data']['value'] for x in granular
                         if x.get('type')=='Point' and x.get('metric')=='http_reqs')
            if requests!=summary['metrics']['http_reqs']['values']['count']:
                raise ValueError('k6 granular and summary request totals differ')
        else:
            stats=list(csv.DictReader((path/'locust-stats.csv').read_text().splitlines()))
            aggregates=[x for x in stats if x.get('Name')=='Aggregated']
            if len(aggregates)!=1:raise ValueError('missing or ambiguous Locust aggregate report')
            count=int(aggregates[0]['Request Count']);failed=int(aggregates[0]['Failure Count'])
            if count<0 or not 0<=failed<=count:raise ValueError('invalid Locust aggregate counts')
            if mode in ('matched','native-ledger','idle-boundary','timing-probe'):
                if any('native_reported' in row and not isinstance(row['native_reported'],bool) for row in records):
                    raise ValueError('invalid native request-event classification')
                reported=sum(row.get('native_reported',True) for row in records)
                if count!=reported:raise ValueError('Locust summary and terminal ledger event counts differ')
                native_totals=dict(native_reported_requests=reported,not_reported_requests=len(records)-reported,native_failures=failed)
                if all('native_failure' in row for row in records):
                    if any(not isinstance(row['native_failure'],bool) or (row['native_failure'] and not row.get('native_reported',True)) for row in records):
                        raise ValueError('invalid native request failure classification')
                    if failed!=sum(row['native_failure'] for row in records):
                        raise ValueError('Locust native failure report and ledger counts differ')
            if not (path/'locust-report.html').stat().st_size:raise ValueError('missing Locust HTML report')
        return dict(verified=True,semantic_complete=True,files=sorted(manifest),**native_totals,
                    workload_exit_code=code,started_at=start,ended_at=end,run_id=run_id,
                    requests=len(records),**(dict(dropped_iterations=dropped,scheduling_evidence='not-applicable-closed-model') if generator=='k6' and mode=='idle-boundary' else {}),manifest_sha256=hashlib.sha256((path/'SHA256SUMS').read_bytes()).hexdigest())
    except (OSError,KeyError,TypeError,OverflowError) as exc:
        raise ValueError('unusable generator artifact: '+str(exc)) from exc


def download_export(namespace: str,pod: str,container: str,path: Path,generator: str,
                    mode: str,run_id: str,*,timeout_seconds=180,attempts=3,fetch=None) -> dict:
    """No ACK or cleanup is performed here. Failed transfers retain .partial files."""
    path=Path(path);path.mkdir(parents=True,exist_ok=True)
    deadline=time.monotonic()+timeout_seconds
    def default_fetch(name,destination,timeout):
        with destination.open('wb') as out:
            result=subprocess.run(['kubectl','exec','-n',namespace,pod,'-c',container,'--',
                                   'cat',REMOTE+'/'+name],stdout=out,stderr=subprocess.PIPE,timeout=timeout)
        if result.returncode:raise ValueError('artifact exec failed: '+result.stderr.decode(errors='replace'))
    fetch=fetch or default_fetch
    for attempt in range(1,attempts+1):
        try:
            remaining=deadline-time.monotonic()
            if remaining<=0:raise ValueError('artifact export deadline reached')
            partial=path/'SHA256SUMS.partial';fetch('SHA256SUMS',partial,min(30,remaining))
            manifest=checksum_manifest(partial.read_text())
            for name,digest in manifest.items():
                remaining=deadline-time.monotonic()
                if remaining<=0:raise ValueError('artifact export deadline reached')
                partial_file=path/(name+'.partial')
                fetch(name,partial_file,min(30,remaining))
                if hashlib.sha256(partial_file.read_bytes()).hexdigest()!=digest:raise ValueError('artifact transfer hash mismatch: '+name)
                partial_file.replace(path/name)
            partial.replace(path/'SHA256SUMS')
            result=validate_export(path,generator,mode,run_id)
            record=dict(attempt=attempt,state='verified',files=len(manifest))
            with (path/'transfer-attempts.ndjson').open('a') as out:out.write(json.dumps(record)+'\n')
            return result
        except (ValueError,OSError,subprocess.TimeoutExpired) as exc:
            record=dict(attempt=attempt,state='failed',error=str(exc))
            with (path/'transfer-attempts.ndjson').open('a') as out:out.write(json.dumps(record)+'\n')
            if attempt==attempts:raise ValueError('generator artifact export failed: '+str(exc)) from exc
    raise ValueError('no artifact export attempts configured')
