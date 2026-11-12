"""Lossless independent samples, bounded on disk and after decompression.

Each append closes one gzip member. A stopped process leaves earlier members
readable; incomplete members still fail gzip CRC/EOF validation. No partial
read is accepted as complete evidence. Legacy plain NDJSON remains readable.
"""
from __future__ import annotations

import gzip
import json
import math
from pathlib import Path

DISK_LIMIT = 256 * 1024 * 1024
LOGICAL_LIMIT = 8 * 1024 * 1024 * 1024


def evidence_path(path):
    path = Path(path)
    plain = path.with_suffix('') if path.suffix == '.gz' else path
    compressed = Path(str(plain)+'.gz')
    if plain.exists() and compressed.exists():
        raise ValueError('ambiguous evidence: both plain and compressed files: '+str(plain))
    return compressed if compressed.exists() else plain


def iter_records(path):
    actual = evidence_path(path)
    if not actual.exists():
        return
    if actual.suffix == '.gz' and actual.stat().st_size == 0:
        raise ValueError('empty compressed sample evidence: '+str(actual))
    opener = gzip.open if actual.suffix == '.gz' else open
    with opener(actual, 'rt', encoding='utf-8') as source:
        for line in source:
            if line.strip():
                yield json.loads(line)


def sample_limits(settings):
    result = (settings.get('capture_sample_max_bytes', DISK_LIMIT),
              settings.get('capture_sample_max_uncompressed_bytes', LOGICAL_LIMIT))
    for value, ceiling in zip(result, (2*1024**3, 8*1024**3)):
        if type(value) is not int or not 0 < value <= ceiling:
            raise ValueError('sampling budget must be a positive integer within supported bounds')
    return result


class BoundedRecords:
    def __init__(self, path, max_bytes, max_uncompressed_bytes):
        self.path = Path(path)
        self.max_bytes, self.max_uncompressed_bytes = max_bytes, max_uncompressed_bytes
        # Do not append a fresh attempt to an existing attempt, including its
        # alternate representation. A writer remains open logically per stream.
        if evidence_path(self.path).exists():
            raise ValueError('refusing existing sample evidence: '+str(self.path))
        self.records = self.stored_bytes = self.uncompressed_bytes = 0
        self.first_received_at = self.last_received_at = None

    def append(self, row):
        raw = (json.dumps(row, ensure_ascii=False)+'\n').encode('utf-8')
        if self.uncompressed_bytes+len(raw) > self.max_uncompressed_bytes:
            raise ValueError('independent sampling uncompressed bound reached')
        encoded = gzip.compress(raw, compresslevel=1, mtime=0) if self.path.suffix=='.gz' else raw
        if self.stored_bytes+len(encoded) > self.max_bytes:
            raise ValueError('independent sampling disk bound reached')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # The first write uses exclusive creation. Errors/short writes remain
        # visible and invalidate capture; no overwrite/rotation/retry is hidden.
        with self.path.open('xb' if not self.records else 'ab') as target:
            if target.write(encoded) != len(encoded):
                raise OSError('short sample write')
        self.records += 1
        self.stored_bytes += len(encoded)
        self.uncompressed_bytes += len(raw)
        self.first_received_at = self.first_received_at or row.get('received_at')
        self.last_received_at = row.get('received_at')

    def validate(self):
        if not self.path.exists() or self.path.stat().st_size != self.stored_bytes:
            raise ValueError('sample integrity: stored size differs from completed writes')
        count=sum(1 for _ in iter_records(self.path))
        if count != self.records:
            raise ValueError('sample integrity: record count differs from completed writes')
        return dict(valid=True,records=count,stored_bytes=self.stored_bytes)

    def status(self):
        return dict(path=str(self.path), encoding='gzip-members' if self.path.suffix=='.gz' else 'ndjson',
                    records=self.records, stored_bytes=self.stored_bytes,
                    uncompressed_bytes=self.uncompressed_bytes,
                    first_received_at=self.first_received_at, last_received_at=self.last_received_at)


def project_capacity(observation, period, seconds, disk_limit, logical_limit):
    # Observation contains one full sampling tick across all selected targets.
    # Doubling allows bounded growth; it is an estimate, never a completeness
    # assertion. Runtime hard limits remain authoritative.
    ticks = math.ceil(seconds/period)
    stored = observation['stored_bytes']*ticks*2
    logical = observation['uncompressed_bytes']*ticks*2
    return dict(expected_seconds=seconds, period_seconds=period, growth_factor=2,
                projected_stored_bytes=stored, projected_uncompressed_bytes=logical,
                sufficient=stored <= disk_limit and logical <= logical_limit)
