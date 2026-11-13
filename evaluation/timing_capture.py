"""Bounded first-observed visibility evidence; timestamps are never ingest times."""
from __future__ import annotations

import base64
import contextlib
import functools
import hashlib
import json
import os
import select
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timezone
from pathlib import Path

from . import runner as r
from .collector_tail_stream import CollectorTailStream, reader_script
from .protocol import KARTHOS_NAMESPACE, source_id
from .sample_records import BoundedRecords, DISK_LIMIT, LOGICAL_LIMIT
from .structured_capture import pod_errors

PERIOD = 5.0
CHUNK_LIMIT = 4 * 1024 * 1024
API_PROBE_TIMEOUT_SECONDS = 90.0


class SourceLifecycleError(ValueError):
    """Source replacement/loss cannot be repaired by retrying transport."""


def utc(ns):
    return datetime.fromtimestamp(ns / 1e9, timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


# Each request contains only a sequence number. Timestamp at the peer before
# returning the matching sequence over the already-established exec stream.
CLOCK_PEER = """import sys, time
for request in sys.stdin:
    stamp = time.time_ns()
    print(request.strip(), stamp, flush=True)
"""


@contextlib.contextmanager
def clock_stream(command):
    """One bounded, non-interactive clock exchange stream."""
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, bufsize=0) as process:
        sequence = 0

        def exchange():
            nonlocal sequence
            sequence += 1
            deadline = time.monotonic() + r.http_timeout(10)
            process.stdin.write(f'{sequence}\n'.encode('ascii'))
            response = b''
            while not response.endswith(b'\n'):
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([process.stdout], [], [], remaining)[0]:
                    raise TimeoutError('clock stream response timed out')
                chunk = os.read(process.stdout.fileno(), 64)
                if not chunk:
                    raise RuntimeError('clock stream closed before response')
                response += chunk
                if len(response) > 64:
                    raise ValueError('clock stream response exceeded 64 bytes')
            fields = response.split()
            if response.count(b'\n') != 1 or len(fields) != 2 or fields[0] != str(sequence).encode('ascii') or not fields[1].isdigit():
                raise ValueError('invalid or unmatched clock stream response')
            return int(fields[1])

        try:
            exchange()  # Establish exec and Python before timing any samples.
            yield exchange
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)


def clock_samples(repair_capture, node_name=None, samples=3, tolerance_seconds=.25, *, persistent=False):
    """Bracket same-node proxy wall time with local wall time; retain every RTT.

    This is a measured bound for this sample, not an NTP synchronization claim.
    Selecting the generator node explicitly is required for a valid clock gate.
    """
    result = dict(valid=False, samples=[], errors=[], node_name=node_name,
                  clock_uncertainty_seconds=None, measured_offset_seconds=None,
                  tolerance_seconds=tolerance_seconds,
                  transport='persistent-kubectl-exec' if persistent else 'single-kubectl-exec')
    candidates = [p for kind, p in repair_capture.pods.items() if kind.split(':',1)[0] == 'agent' and p.get('spec', {}).get('nodeName') == node_name]
    if not node_name or len(candidates) != 1:
        result['errors'].append('same-generator-node Python proxy unavailable or ambiguous')
        return result
    if not 3 <= samples <= 5:
        raise ValueError('clock samples must be between three and five')
    args = ['exec', '-n', KARTHOS_NAMESPACE, candidates[0]['metadata']['name'],
            '-c', 'diag-kubelet-proxy']
    def single_exchange():
        response = r.kube(*args, '--', 'python3', '-c',
                          'import time; print(time.time_ns())', timeout=10, quiet=True)
        return int(response.stdout.strip())

    stream = (clock_stream(['kubectl', *args, '-i', '--', 'python3', '-u', '-c', CLOCK_PEER])
              if persistent else contextlib.nullcontext(single_exchange))
    try:
        with stream as exchange:
            for _ in range(samples):
                started = time.time_ns()
                remote = exchange()
                ended = time.time_ns()
                if ended < started:
                    raise ValueError('local wall clock regressed')
                offset = (2 * remote - started - ended) / 2e9
                uncertainty = (ended - started) / 2e9
                result['samples'].append(dict(requested_at_ns=started, received_at_ns=ended,
                    requested_at=utc(started), received_at=utc(ended), remote_at_ns=remote,
                    measured_offset_seconds=offset, half_rtt_seconds=uncertainty,
                    absolute_offset_bound_seconds=abs(offset) + uncertainty))
    except Exception as exc:
        result['errors'].append(str(exc))
    if result['samples']:
        best = min(result['samples'], key=lambda x: x['half_rtt_seconds'])
        result['measured_offset_seconds'] = best['measured_offset_seconds']
        # Conservative across all collected checks, including wall-clock skew.
        result['clock_uncertainty_seconds'] = max(x['absolute_offset_bound_seconds'] for x in result['samples'])
        if result['clock_uncertainty_seconds'] > tolerance_seconds:
            result['errors'].append('measured node/local clock bound exceeds tolerance')
    result['valid'] = len(result['samples']) == samples and not result['errors']
    return result


class AvailabilityCapture:
    def __init__(self, cell, path, repair_capture):
        self.cell, self.path, self.repair = cell, Path(path), repair_capture
        self.path.mkdir(parents=True, exist_ok=True)
        self.writer = BoundedRecords(self.path/'availability.ndjson.gz', DISK_LIMIT, LOGICAL_LIMIT)
        self.status = dict(valid=False, errors=[], period_seconds=PERIOD, polls=[], streams={},
                           exact_ingestion_time_supported=False, overhead_seconds=0., worker_stopped=True)
        self._stop = threading.Event()
        self._thread = None
        self._threads = []
        self._finished_workers = set()
        self._record_lock = threading.RLock()
        self._stack = contextlib.ExitStack()
        self._files = {}
        self._api_targets = {}
        self.status['transport_policy'] = ('sha256-framed-gzip1' if self._multinode()
                                           else 'sha256-framed-binary1')
        if self._multinode():
            self.status['gateway_transport'] = 'persistent-sequenced-kubectl-exec1'
            self.status['agent_transport'] = 'persistent-sequenced-kubectl-exec1'
        self.status['transport_recoveries'] = 0
        self.status['api_probe_policy'] = dict(version='fixed-evaluation1',
            mode='repeat fixed evaluation point until visible or expired',
            timeout_seconds=API_PROBE_TIMEOUT_SECONDS, step='15s',
            interpretation='API query visibility, including storage query latency policy; not ingestion time')
        self._stopped = False
        self._worker_finished_normally = False
        self._expected = {'vm', *(f'api:{name}' for name in ('servicegraph.request.count', 'servicegraph.request.error.count', 'servicegraph.request.duration'))}
        self._collector_streams = {
            f'{kind}:{filename}': (kind, pod, filename)
            for kind, pod in self.repair.pods.items()
            for filename in (('metrics.json', 'traces.json') if kind == 'gateway' else ('metrics.json',))
        }
        self._expected.update(self._collector_streams)

    def _save(self):
        self.status['writer'] = self.writer.status()
        temporary = self.path/'availability-status.json.tmp'
        temporary.write_text(json.dumps(self.status, indent=2)+'\n')
        temporary.replace(self.path/'availability-status.json')

    def _record(self, source, query_id, requested, response=None, error=None, **extra):
        with self._record_lock:
            return self._record_locked(source,query_id,requested,response,error,**extra)

    def _record_locked(self, source, query_id, requested, response=None, error=None, **extra):
        received = time.time_ns()
        row = dict(source=source, source_id=source_id(self.cell.source), query_id=query_id,
                   requested_at_ns=requested, received_at_ns=received,
                   requested_at=utc(requested), received_at=utc(received), **extra)
        if response is not None:
            row['response'] = response
        if error is not None:
            row['error'] = str(error)
        with self._record_lock:
            self.writer.append(row)
        stream = self.status['streams'].setdefault(query_id, dict(polls=0, nonempty=0))
        stream['polls'] += 1
        if response or extra.get('body'):
            stream['nonempty'] += 1
        if error is not None:
            self.status['errors'].append(f'{query_id}: {error}')
        return row

    def start(self):
        started = time.monotonic()
        try:
            self.api = self._stack.enter_context(r.forward('deployment/karthos-backend', KARTHOS_NAMESPACE, 28981, 8080))
            services = r.kube_json('get', 'services', '-n', KARTHOS_NAMESPACE)['items']
            names = [s['metadata']['name'] for s in services if 'victoria' in s['metadata']['name']]
            if len(names) != 1:
                raise ValueError('VictoriaMetrics service unavailable or ambiguous')
            self.vm = self._stack.enter_context(r.forward('service/'+names[0], KARTHOS_NAMESPACE, 28928, 8428))
            self._thread = threading.Thread(target=self._loop, args=(self._poll_remote,'remote') if self._multinode() else (), name='timing-availability', daemon=True)
            self._threads=[self._thread]
            if self._multinode():
                self.status['gateway_handshake_seconds'] = {}
                self.status['agent_handshake_seconds'] = {}
                for label, (kind, pod, filename) in self._collector_streams.items():
                    command = ['kubectl', 'exec', '-n', KARTHOS_NAMESPACE, pod['metadata']['name'],
                               '-c', 'diag-reader', '-i', '--', 'sh', '-c', reader_script(filename, CHUNK_LIMIT)]
                    # Negotiate exec once per file. Polling then spends its bounded
                    # interval on reads, instead of repeatedly opening Kubernetes streams.
                    stream = self._stack.enter_context(CollectorTailStream(command, CHUNK_LIMIT*2))
                    handshake_started = time.monotonic()
                    stream.handshake(r.http_timeout(10))
                    collector_kind = kind.split(':', 1)[0]
                    self.status[collector_kind+'_handshake_seconds'][label] = time.monotonic()-handshake_started
                    poll = functools.partial(self._poll_collector, kind, pod, filename, stream)
                    self._threads.append(threading.Thread(target=self._loop, args=(poll, label),
                                                          name='availability-'+label, daemon=True))
            self.status['worker_stopped'] = False
            for worker in self._threads:worker.start()
            self.status['setup_seconds'] = time.monotonic()-started
            return self
        except Exception as exc:
            self.status['errors'].append('setup: '+str(exc))
            self._stack.close()
            self._save()
            raise

    def _check_cancel(self):
        if self._stop.is_set():
            raise InterruptedError('availability capture stopping')

    def _http_bytes(self, url, deadline, maximum=32*1024*1024):
        self._check_cancel()
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            raise TimeoutError('availability HTTP operation exceeded five-second bound')
        with urllib.request.urlopen(url, timeout=min(2., remaining)) as response:
            chunks = []
            total = 0
            # HTTPResponse.read1 returns after at most one underlying read;
            # repeated tiny network writes cannot extend a read(size) forever.
            read = getattr(response, 'read1', response.read)
            while True:
                self._check_cancel()
                if time.monotonic() >= deadline:
                    raise TimeoutError('availability response exceeded five-second bound')
                chunk = read(min(65536, maximum-total+1))
                if not chunk:
                    return b''.join(chunks)
                chunks.append(chunk)
                total += len(chunk)
                if total > maximum:
                    raise ValueError('availability response exceeds byte bound')

    def _api_pages(self, route, params, key):
        deadline = time.monotonic()+5.
        items, seen, token = [], set(), None
        total_bytes = 0
        for page_number in range(1, 1001):
            self._check_cancel()
            query = dict(params, page_size='1000')
            if token:
                query['next_token'] = token
            url = self.api+route+'?'+urllib.parse.urlencode(query)
            raw = self._http_bytes(url, deadline)
            total_bytes += len(raw)
            if total_bytes > 32*1024*1024:
                raise ValueError('availability API pages exceed total byte bound')
            page = json.loads(raw)
            self._record('api-pages', 'api-page:'+params.get('metric_name', route), time.time_ns(),
                         page, params=query, page_number=page_number)
            data = page.get('data')
            if not isinstance(data, dict) or not isinstance(data.get(key), list):
                raise ValueError('invalid availability API page envelope')
            items.extend(data[key])
            token = page.get('next_token')
            if not token:
                return {'data': {key: items, 'pages': page_number,
                    'query': data.get('query'), 'snapshot': data.get('snapshot')}}
            if token in seen:
                raise ValueError('repeated availability API pagination token')
            seen.add(token)
        raise ValueError('availability API pagination exceeded page bound')

    def _vm(self):
        if self._stop.is_set():
            return
        requested = time.time_ns()
        server = 'checkoutservice' if self.cell.demo == 'boutique' else 'checkout'
        selector = '{__name__=~"servicegraph_request_(count|error_count|duration.*)",source='+json.dumps(source_id(self.cell.source))+',edge_client="frontend",edge_server='+json.dumps(server)+'}'
        params = {'match[]': selector, 'start': str(requested/1e9-120), 'end': str(requested/1e9)}
        url = self.vm+'/api/v1/export?'+urllib.parse.urlencode(params)
        try:
            raw = self._http_bytes(url, time.monotonic()+5.)
            rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
            self._record('vm', 'vm', requested, rows, url=url, params=params)
        except InterruptedError:
            return
        except Exception as exc:
            self._record('vm', 'vm', requested, error=exc, url=url, params=params)

    def _api(self, name):
        if self._stop.is_set():
            return
        requested = time.time_ns()
        # A moving one-point query always lies inside VictoriaMetrics' recent
        # point adjustment interval. Revisit an identical evaluation point so
        # it can mature without changing the product's query policy or step.
        previous_probe = None
        target = self._api_targets.get(name)
        if target is not None and (target['visible'] or
                requested-target['at_ns'] >= API_PROBE_TIMEOUT_SECONDS*1e9):
            previous_probe = dict(evaluation_at=utc(target['at_ns']),
                reason='visible' if target['visible'] else 'empty_after_timeout')
            target = None
        if target is None:
            target = dict(at_ns=requested, visible=False)
            self._api_targets[name] = target
        at = utc(target['at_ns'])
        metadata = dict(evaluation_at=at,
            evaluation_age_seconds=(requested-target['at_ns'])/1e9,
            probe_policy='fixed-evaluation1', previous_probe=previous_probe)
        params = dict(metric_name=name, source=source_id(self.cell.source), step='15s', **{'from':at, 'to':at})
        if name.endswith('.duration'):
            params['window'] = '60s'
        else:
            params['aggregation'] = 'latest'
        try:
            response = self._api_pages('/api/v1/metrics', params, 'samples')
            self._record('api', 'api:'+name, requested, response, params=params, **metadata)
            target['visible'] = bool(response.get('data', {}).get('samples'))
            # A response envelope alone does not constitute nonempty evidence.
            if not response.get('data', {}).get('samples'):
                self.status['streams']['api:'+name]['nonempty'] -= 1
        except InterruptedError:
            return
        except Exception as exc:
            self._record('api', 'api:'+name, requested, error=exc, params=params, **metadata)
        # The existing bounded writer retains equivalent storage responses for
        # every API evaluation, including empty/error polls.
        if not self._stop.is_set():
            try:
                from .gke.poll_capture import compiled_storage
                deadline = time.monotonic()+5.
                def fetch(route, query):
                    raw = self._http_bytes(self.vm+route+'?'+urllib.parse.urlencode(query), deadline)
                    response = json.loads(raw)
                    self._record('api-storage-responses', 'api-storage-response:'+name, requested,
                                 response, params=query, route=route, **metadata)
                    return response
                compiled, document = compiled_storage(params, r.ROOT, fetch, deadline, target.get('compiled'))
                target['compiled'] = compiled
                self._record('api-storage', 'api-storage:'+name, requested,
                             dict(compiled=compiled, **document), params=params, **metadata)
            except InterruptedError:
                return
            except Exception as exc:
                self._record('api-storage', 'api-storage:'+name, requested, error=exc, params=params, **metadata)

    def _tail(self, kind, pod, filename, stream=None):
        if self._stop.is_set():
            return
        if filename not in ('metrics.json', 'traces.json'):
            raise ValueError('unsafe diagnostic filename')
        key = f'{kind}:{filename}'
        state = self._files.setdefault(key, dict(inode=None, offset=0, carry=b''))
        offset = state['offset']
        compressed = self._multinode()
        transfer = 'gzip -1 -c "$chunk" | base64' if compressed else 'base64 "$chunk"'
        # Only static filenames and integer offsets enter shell code. Snapshot
        # size bounds the read; later appends are observed on the next poll.
        script = f'''set -eu
f=/diag/{filename}
if [ ! -f "$f" ]; then printf 'missing\\n'; exit 0; fi
for p in /diag/{filename[:-5]}*.json; do [ "$p" = "$f" ] || {{ echo rotation >&2; exit 40; }}; done
meta=$(stat -c '%i:%s' "$f")
size=${{meta#*:}}
n=$((size-{offset}))
[ "$n" -ge 0 ] || exit 41
[ "$n" -le {CHUNK_LIMIT} ] || n={CHUNK_LIMIT}
chunk=$(mktemp /tmp/evaluation-read.XXXXXX)
trap 'rm -f "$chunk"' EXIT HUP INT TERM
if [ "$n" -gt 0 ]; then tail -c +{offset+1} "$f" | head -c "$n" > "$chunk"; fi
digest=$(sha256sum "$chunk")
digest=${{digest%% *}}
printf '%s:%s\\n' "$meta" "$digest"
{transfer}'''
        requested = time.time_ns()
        deadline = time.monotonic()+PERIOD
        attempts = []
        for attempt in range(1, 4):
            response = None
            try:
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('collector read retry exceeded polling bound')
                timeout = r.http_timeout(min(2., remaining))
                if stream is not None:
                    response = stream.exchange(offset, timeout)
                else:
                    response = r.kube('exec', '-n', KARTHOS_NAMESPACE, pod['metadata']['name'],
                        '-c', 'diag-reader', '--', 'sh', '-c', script,
                        timeout=timeout, quiet=True, binary=True, check=False)
                if response.returncode in (40, 41):
                    raise SourceLifecycleError('collector file '+('rotation' if response.returncode == 40 else 'truncation')+': '+key)
                if response.returncode:
                    raise RuntimeError('collector binary read exited '+str(response.returncode))
                metadata_bytes, encoded = response.stdout.split(b'\n', 1)
                metadata = metadata_bytes.decode('ascii')
                if metadata == 'missing':
                    if state['inode'] is not None:
                        raise SourceLifecycleError('collector file disappeared: '+key)
                    self._record('collector-tail', key, requested, body='', offset=offset,
                                 file=filename, exists=False, collector_uid=pod['metadata']['uid'],
                                 collector_kind=kind.split(':',1)[0], node_name=pod.get('spec',{}).get('nodeName'))
                    return
                inode_text, size_text, digest = metadata.split(':')
                inode, size = int(inode_text), int(size_text)
                if state['inode'] is not None and inode != state['inode']:
                    raise SourceLifecycleError('collector file rotation: '+key)
                if size < offset:
                    raise SourceLifecycleError('collector file truncation: '+key)
                if len(digest) != 64 or any(x not in '0123456789abcdef' for x in digest):
                    raise ValueError('invalid collector checksum frame: '+key)
                wire = base64.b64decode(b''.join(encoded.split()), validate=True)
                expected = min(size-offset, CHUNK_LIMIT)
                if compressed:
                    decoder = zlib.decompressobj(wbits=31)
                    raw = decoder.decompress(wire, expected+1)
                    if not decoder.eof or decoder.unused_data or len(raw) > expected:
                        raise ValueError('invalid compressed collector frame: '+key)
                else:
                    raw = wire
                if len(raw) != expected:
                    raise ValueError('collector short or oversized read: '+key)
                if hashlib.sha256(raw).hexdigest() != digest:
                    raise ValueError('collector transfer checksum mismatch: '+key)
                break
            except Exception as exc:
                failure = dict(attempt=attempt, attempt_error=str(exc), offset=offset, failure_boundary='kubectl-transfer',
                    transfer_encoding='gzip+base64' if compressed else 'base64',
                    stdout_base64=base64.b64encode(response.stdout if response is not None else getattr(exc, 'output', b'') or b'').decode('ascii'),
                    stderr_base64=base64.b64encode(response.stderr if response is not None else getattr(exc, 'stderr', b'') or b'').decode('ascii'))
                attempts.append(failure)
                self._record('collector-read', 'read-attempt:'+key, requested, **failure)
                if isinstance(exc, SourceLifecycleError) or attempt == 3 or time.monotonic() >= deadline:
                    raise
        if state['inode'] is not None and inode != state['inode']:
            raise ValueError('collector file rotation: '+key)
        if size < offset:
            raise ValueError('collector file truncation: '+key)
        if attempts:
            self.status['transport_recoveries'] += 1
        joined = state['carry']+raw
        complete, separator, carry = joined.rpartition(b'\n')
        body = complete+b'\n' if separator else b''
        if not separator:
            carry = joined
        try:
            for line in body.splitlines():
                if line.strip():
                    json.loads(line)
            decoded_body = body.decode('utf-8')
        except (ValueError, UnicodeError) as exc:
            self._record('collector-read', 'source-invalid:'+key, requested,
                attempt_error=str(exc), failure_boundary='collector-file-json', raw_chunk_base64=base64.b64encode(raw).decode('ascii'),
                offset=offset, inode=inode, chunk_sha256=digest)
            raise
        if len(carry) > 64*1024*1024:
            raise ValueError('collector incomplete line exceeds memory bound: '+key)
        state.update(inode=inode, offset=offset+len(raw), carry=carry, backlog=size-offset-len(raw))
        self._record('collector-tail', key, requested, body=decoded_body, chunk_sha256=digest,
                     transfer_attempts=len(attempts)+1,
                     transport_stderr_base64=base64.b64encode(response.stderr).decode('ascii'),
                     raw_chunk_base64=base64.b64encode(raw).decode('ascii'), offset=offset,
                     end_offset=state['offset'], backlog_bytes=size-state['offset'],
                     partial_line_bytes=len(carry), inode=inode, file=filename,
                     collector_uid=pod['metadata']['uid'], collector_kind=kind.split(':',1)[0], node_name=pod.get('spec',{}).get('nodeName'))

    def _multinode(self):
        return any(':' in key for key in self.repair.pods)

    def _poll_remote(self):
        self._vm()
        for name in ('servicegraph.request.count', 'servicegraph.request.error.count', 'servicegraph.request.duration'):
            self._api(name)

    def _poll_collector(self,kind,before,filename=None,stream=None):
        if self._stop.is_set():
            return
        current = json.loads(r.kube('get', 'pod', before['metadata']['name'], '-n', KARTHOS_NAMESPACE, '-o', 'json', timeout=5, quiet=True).stdout)
        errors = pod_errors(before, current)
        if errors:
            raise ValueError('; '.join(errors))
        files = (filename,) if filename is not None else (
            ('metrics.json', 'traces.json') if kind == 'gateway' else ('metrics.json',))
        for filename in files:
            if self._stop.is_set():
                return
            requested = time.time_ns()
            try:
                self._tail(kind, before, filename, stream)
            except Exception as exc:
                self._record('collector-tail', f'{kind}:{filename}', requested, error=exc)

    def _poll(self):
        self._poll_remote()
        for kind,before in self.repair.pods.items():
            self._poll_collector(kind,before)

    def _loop(self,poller=None,label=None):
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                (poller or self._poll)()
            except Exception as exc:
                self.status['errors'].append('poll: '+str(exc))
            elapsed = time.monotonic()-started
            self.status['polls'].append(dict(started_monotonic=started, elapsed_seconds=elapsed, worker=label))
            self.status['overhead_seconds'] += elapsed
            if elapsed > PERIOD:
                self.status['errors'].append('availability polling cadence exceeded five seconds')
            with self._record_lock:self._save()
            self._stop.wait(max(0, PERIOD-elapsed))
        # If bounded stop returned while a request was in flight, close only
        # after the worker ceases using the forwarded endpoints.
        if not self._multinode() and getattr(self.repair,'settings',{}).get('capture_finalization_version')!=2:self._stack.close()
        self._finished_workers.add(threading.current_thread().name)
        self._worker_finished_normally = True
        if not self._multinode():self.status['worker_stopped'] = True

    def _finish_frozen_tails(self):
        """Consume the sealed boundary prefix with actual late observation times."""
        import hashlib
        frozen=self.repair.freeze_snapshots()
        self.status['finalization']={'version':2,'allowance_seconds':30,'streams':{}}
        for key,(kind,pod,filename) in self._collector_streams.items():
            try:
                component=frozen['components'][kind]
                receipt=component['files'][filename]
                if component.get('errors'):
                    raise ValueError('frozen source invalid: '+'; '.join(component['errors']))
                if component['collector_uid']!=pod['metadata']['uid']:
                    raise ValueError('final snapshot collector UID changed')
                path=Path(component['raw_directory'])/filename
                if not receipt.get('complete'):
                    raise ValueError('incomplete frozen snapshot: '+str(receipt.get('reason','source unavailable')))
                if path.stat().st_size!=receipt['sealed_bytes']:
                    raise ValueError('frozen snapshot size changed')
                with path.open('rb') as handle:
                    if hashlib.file_digest(handle,'sha256').hexdigest()!=receipt['sha256']:
                        raise ValueError('frozen snapshot checksum mismatch')
                state=self._files.setdefault(key,dict(inode=None,offset=0,carry=b''))
                if state['inode'] is not None and state['inode']!=receipt['inode']:
                    raise ValueError('final snapshot inode changed')
                if state['offset']>receipt['sealed_bytes']:
                    raise ValueError('final snapshot source truncated')
                with path.open('rb') as handle:
                    handle.seek(state['offset'])
                    while raw:=handle.read(CHUNK_LIMIT):
                        requested=time.time_ns()
                        offset=state['offset']
                        complete,separator,carry=(state['carry']+raw).rpartition(b'\n')
                        body=complete+b'\n' if separator else b''
                        if not separator:carry=state['carry']+raw
                        for line in body.splitlines():
                            if line.strip():json.loads(line)
                        state.update(inode=receipt['inode'],offset=offset+len(raw),carry=carry,backlog=receipt['sealed_bytes']-offset-len(raw))
                        self._record('collector-tail',key,requested,body=body.decode('utf-8'),
                            phase='finalization',collector_uid=pod['metadata']['uid'],
                            collector_kind=kind.split(':',1)[0],node_name=pod.get('spec',{}).get('nodeName'),
                            inode=receipt['inode'],offset=offset,end_offset=state['offset'],
                            backlog_bytes=state['backlog'],partial_line_bytes=len(carry),file=filename,
                            raw_chunk_base64=base64.b64encode(raw).decode('ascii'),chunk_sha256=hashlib.sha256(raw).hexdigest(),
                            frozen_cutoff_bytes=receipt['cutoff_bytes'],snapshot_completed_at_ns=receipt['completed_at_ns'])
                self.status['finalization']['streams'][key]=receipt
            except Exception as exc:
                self.status['errors'].append('finalization '+key+': '+str(exc))

    def stop(self):
        if self._stopped:
            return self.status
        self._stop.set()
        if self._thread is not None:
            deadline=time.monotonic()+45
            for worker in self._threads or [self._thread]:worker.join(timeout=max(0.,deadline-time.monotonic()))
            if any(worker.is_alive() for worker in self._threads or [self._thread]):
                self.status['worker_stopped'] = False
                self.status['errors'].append('availability worker still alive after bounded stop')
                self._save()
                return self.status
            if not self._worker_finished_normally or (self._multinode() and any(worker.name not in self._finished_workers for worker in self._threads)):
                self.status['errors'].append('availability worker exited unexpectedly')
        self.status['worker_stopped'] = True
        if getattr(self.repair,'settings',{}).get('capture_finalization_version')==2:
            self._finish_frozen_tails()
        self._stack.close()
        for name in self._expected:
            if not self.status['streams'].get(name, {}).get('nonempty'):
                self.status['errors'].append('empty availability stream: '+name)
        for name, state in self._files.items():
            if state.get('backlog'):
                self.status['errors'].append('unread final collector backlog: '+name)
            if state['carry']:
                self.status['errors'].append('incomplete final JSON line: '+name)
        try:
            self.status['integrity'] = self.writer.validate()
        except Exception as exc:
            self.status['errors'].append(str(exc))
        self.status['valid'] = bool(self.status['polls']) and not self.status['errors']
        self._stopped = True
        self._save()
        return self.status
