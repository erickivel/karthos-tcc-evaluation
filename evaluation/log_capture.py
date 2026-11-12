"""Watcher-free Kubernetes log snapshots with nanosecond-safe overlap handling.

kubectl serializes since-time at whole-second precision. Each query therefore
starts at an explicit UTC second and may replay every record in that second.
A multiset keyed by exact timestamp nanoseconds and payload removes only replay,
retaining distinct timestamps and real identical entries. Any malformed/error
snapshot invalidates capture.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import re
import subprocess
import threading
from pathlib import Path

STAMP = re.compile(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$')


def timestamp_ns(value: str) -> int:
    match = STAMP.fullmatch(value)
    if not match:
        raise ValueError(f'invalid RFC3339Nano timestamp: {value!r}')
    seconds, fraction, zone = match.groups()
    date = datetime.fromisoformat(seconds + ('+00:00' if zone == 'Z' else zone))
    return int(date.timestamp()) * 1_000_000_000 + int((fraction or '').ljust(9, '0'))


def floor_second_ns(value: int) -> int:
    return value // 1_000_000_000 * 1_000_000_000


def utc_second(value: int) -> str:
    return datetime.fromtimestamp(value // 1_000_000_000, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


class PollingLogCapture:
    def __init__(self, ns, pod, container, log_path, started_at, timestamps=True,
                 poll_interval=5, timeout=20, max_bytes=None, snapshot=None):
        if max_bytes is not None and (not isinstance(max_bytes,int) or isinstance(max_bytes,bool) or max_bytes<=0):
            raise ValueError('max_bytes must be a positive integer or None')
        self.max_bytes, self.limit_exceeded = max_bytes, False
        self.ns, self.pod, self.container = ns, pod, container
        self.log_path = Path(log_path)
        self.started_at, self.timestamps = started_at, timestamps
        self.poll_interval, self.timeout = poll_interval, timeout
        self.snapshot = snapshot
        self.max_seen_ns = timestamp_ns(started_at)
        self.cursor_ns = floor_second_ns(self.max_seen_ns)
        self.cursor = utc_second(self.cursor_ns)
        self.boundary = Counter()
        self.stop_event = threading.Event()
        self.thread = None
        self.errors = []
        self.polls = 0
        self.started = False
        self.stopped = False
        self.exited_before_stop = False
        self._stop_lock = threading.Lock()

    def start(self):
        if self.started or self.stopped:
            raise RuntimeError('log capture cannot be restarted')
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text('')
        self.log_path.with_suffix('.stderr').write_text('')
        self.started = True
        self.thread = threading.Thread(target=self._loop, name=f'logs-{self.pod}-{self.container}', daemon=True)
        self.thread.start()
        return self

    def _retained_bytes(self):
        return sum(path.stat().st_size for path in (self.log_path,self.log_path.with_suffix('.stderr')) if path.exists())

    def _check_capacity(self, incoming_bytes):
        if self.max_bytes is not None and self._retained_bytes()+incoming_bytes>self.max_bytes:
            self.limit_exceeded=True
            failure=ValueError(f'combined log/stderr byte limit exceeded: {self.max_bytes}')
            self._error(failure)
            raise failure

    def _poll_once(self):
        if self.limit_exceeded:raise ValueError('log byte limit previously exceeded')
        self.polls += 1
        if self.snapshot is not None:
            result = self.snapshot(self.ns, self.pod, self.container, self.cursor, self.timeout)
        else:
            result = subprocess.run(
                ['kubectl', 'logs', '-n', self.ns, self.pod, '-c', self.container,
                 '--timestamps', '--since-time=' + self.cursor],
                capture_output=True, timeout=self.timeout, check=False,
            )
        # Decode bytes explicitly: subprocess text mode would normalize CRLF
        # and alter the raw timestamped evidence written to disk.
        stdout = result.stdout.decode('utf-8') if isinstance(result.stdout, bytes) else result.stdout
        stderr = result.stderr.decode('utf-8') if isinstance(result.stderr, bytes) else result.stderr
        if stderr:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            data=stderr.encode('utf-8')
            self._check_capacity(len(data))
            with self.log_path.with_suffix('.stderr').open('ab') as errors:
                errors.write(data)
        if result.returncode or stderr.strip():
            raise RuntimeError(f'kubectl logs returned {result.returncode}: {stderr.strip()}')
        lines = []
        previous = self.cursor_ns
        for raw in stdout.splitlines(keepends=True):
            stamp, separator, payload = raw.partition(' ')
            if not separator:
                raise ValueError(f'log line lacks timestamp: {raw[:160]!r}')
            value = timestamp_ns(stamp)
            second = floor_second_ns(value)
            if second < previous:
                raise ValueError(f'out-of-order log timestamp {stamp}; requested floor={self.cursor}')
            if 'failed to create fsnotify watcher' in payload.lower():
                raise ValueError('fsnotify watcher failure in log snapshot')
            previous = second
            lines.append((value, stamp, payload, raw))
        # Validate the complete snapshot before appending any part of it.
        remaining = self.boundary.copy()
        output = []
        max_seen_ns = max([self.max_seen_ns] + [value for value, _, _, _ in lines])
        cursor_ns = floor_second_ns(max_seen_ns)
        snapshot_boundary = Counter()
        for value, stamp, payload, raw in lines:
            key = (value, payload)
            if floor_second_ns(value) == cursor_ns:
                snapshot_boundary[key] += 1
            if floor_second_ns(value) == self.cursor_ns and remaining[key]:
                remaining[key] -= 1
                continue
            output.append(raw if self.timestamps else payload)
        # When still in the same second, preserve prior counts if a snapshot is
        # empty/partial; when advancing, retain all records in the new second.
        boundary = (self.boundary | snapshot_boundary if cursor_ns == self.cursor_ns
                    else snapshot_boundary)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        data=''.join(output).encode('utf-8')
        self._check_capacity(len(data))
        with self.log_path.open('ab') as output_file:
            output_file.write(data)
        self.max_seen_ns = max_seen_ns
        self.cursor_ns, self.cursor, self.boundary = cursor_ns, utc_second(cursor_ns), boundary

    def _error(self, exc):
        self.errors.append(f'{type(exc).__name__}: {exc}')

    def _loop(self):
        try:
            while not self.stop_event.is_set():
                self._poll_once()
                if self.stop_event.wait(self.poll_interval):
                    break
        except BaseException as exc:
            if not self.limit_exceeded:self._error(exc)
            self.exited_before_stop = not self.stop_event.is_set()

    def stop(self):
        with self._stop_lock:
            if self.stopped:
                return self.status()
            self.stop_event.set()
            if self.thread:
                self.thread.join(timeout=self.timeout + 1)
                if self.thread.is_alive():
                    self._error(TimeoutError('polling thread did not stop within bounded wait'))
                elif not self.limit_exceeded:
                    # A final bounded snapshot captures records emitted since the
                    # previous poll. A failed earlier poll remains invalid evidence.
                    try:
                        self._poll_once()
                    except BaseException as exc:
                        self._error(exc)
            self.stopped = True
            return self.status()

    def status(self):
        return {
            'stream': f'{self.ns}__{self.pod}__{self.container}',
            'mode': 'polling', 'exited_before_stop': self.exited_before_stop,
            'returncode': 1 if self.errors else (0 if self.stopped else None),
            'errors': list(self.errors), 'error': '; '.join(self.errors) or None,
            'polls': self.polls, 'stopped': self.stopped,
            'cursor': self.cursor, 'started_at': self.started_at,
            'max_bytes': self.max_bytes, 'retained_bytes': self._retained_bytes(),
            'limit_exceeded': self.limit_exceeded,
        }
