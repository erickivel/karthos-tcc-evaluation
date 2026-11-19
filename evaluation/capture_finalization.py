"""Bounded immutable JSON-lines prefixes over an append-only source.

The first observed size fixes the population. Only the record intersecting that
size may be completed later; completion never inherits an earlier timestamp.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

DEFAULT_FINALIZATION_SECONDS = 30.0
MAX_CAPTURE_BYTES = 512 * 1024 * 1024
MAX_RECORD_BYTES = 16 * 1024 * 1024


class SnapshotIncomplete(ValueError):
    def __init__(self, message, receipt):
        super().__init__(message)
        self.receipt = receipt


def freeze_prefix(read, destination, *, timeout_seconds=DEFAULT_FINALIZATION_SECONDS,
                  maximum_bytes=MAX_CAPTURE_BYTES, expected_inode=None, deadline=None):
    """Read ``{inode,size,data,requested_at_ns,received_at_ns}`` frames by offset.

    The reader must validate transfer lengths and hashes. Failed partial files
    remain for explicit quarantine; this function never overwrites originals.
    """
    destination = Path(destination)
    expires = min(time.monotonic()+timeout_seconds,
                  deadline if deadline is not None else float('inf'))
    receipt = {'version':2, 'complete':False, 'sealed_bytes':0,
               'cutoff_bytes':None, 'inode':expected_inode}
    digest = hashlib.sha256()
    pending = bytearray()
    offset = 0
    previous_size = 0
    with destination.open('xb') as output:
        try:
            while True:
                if time.monotonic() >= expires:
                    raise ValueError('snapshot finalization deadline exhausted')
                frame = read(offset)
                inode, size, chunk = frame['inode'], frame['size'], frame['data']
                if receipt['inode'] is not None and inode != receipt['inode']:
                    raise ValueError('snapshot inode changed')
                receipt['inode'] = inode
                if size < previous_size or size < offset:
                    raise ValueError('snapshot source truncated')
                if size > maximum_bytes or offset+len(chunk) > size:
                    raise ValueError('snapshot source byte bound exceeded')
                previous_size = size
                if receipt['cutoff_bytes'] is None:
                    receipt.update(cutoff_bytes=size,
                        cutoff_requested_at_ns=frame['requested_at_ns'],
                        cutoff_received_at_ns=frame['received_at_ns'])
                cutoff = receipt['cutoff_bytes']
                # Consume the frozen prefix and, at most, one boundary record.
                before = max(0, cutoff-offset)
                take = chunk[:before]
                after = chunk[before:] if len(chunk) > before else b''
                if after and (pending or take) and not (pending+take).endswith(b'\n'):
                    newline = after.find(b'\n')
                    take += after if newline < 0 else after[:newline+1]
                elif offset >= cutoff and pending:
                    newline = chunk.find(b'\n')
                    take = chunk if newline < 0 else chunk[:newline+1]
                if take:
                    if offset+len(take) > maximum_bytes:
                        raise ValueError('snapshot source byte bound exceeded')
                    output.write(take)
                    output.flush()
                    digest.update(take)
                    offset += len(take)
                    pending.extend(take)
                    while True:
                        newline = pending.find(b'\n')
                        if newline < 0:
                            break
                        line = bytes(pending[:newline])
                        del pending[:newline+1]
                        if len(line)+1 > MAX_RECORD_BYTES:
                            raise ValueError('snapshot JSON record byte bound exceeded')
                        if line.strip():
                            try:
                                value = json.loads(line)
                            except (ValueError, UnicodeError) as exc:
                                raise ValueError('snapshot invalid JSON record') from exc
                            if not isinstance(value, dict):
                                raise ValueError('snapshot JSON record must be an object')
                    if len(pending) > MAX_RECORD_BYTES:
                        raise ValueError('snapshot JSON record byte bound exceeded')
                receipt.update(sealed_bytes=offset, sha256=digest.hexdigest(),
                               completed_at_ns=frame['received_at_ns'])
                if time.monotonic() >= expires:
                    raise ValueError('snapshot finalization deadline exhausted after read')
                if offset >= cutoff and not pending:
                    receipt['complete'] = True
                    return receipt
                if not chunk:
                    time.sleep(min(0.025, max(0, expires-time.monotonic())))
        except Exception as exc:
            receipt.update(sealed_bytes=offset, sha256=digest.hexdigest(),
                           remainder_bytes=len(pending), reason=str(exc))
            raise SnapshotIncomplete(str(exc), receipt) from exc


def decode_frame(response, offset, *, compressed=True, chunk_limit=4*1024*1024):
    """Decode the existing inode/size/SHA256 collector protocol fail-closed."""
    import base64
    import zlib
    if response.returncode in (40, 41):
        raise ValueError('collector '+('rotation' if response.returncode == 40 else 'truncation'))
    if response.returncode:
        raise ValueError('collector transfer exit '+str(response.returncode))
    metadata, encoded = response.stdout.split(b'\n', 1)
    if metadata == b'missing':
        raise ValueError('collector file missing')
    inode_text, size_text, digest = metadata.decode('ascii').split(':')
    inode, size = int(inode_text), int(size_text)
    if inode <= 0 or size < offset or offset < 0:
        raise ValueError('collector truncation or invalid identity')
    if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('collector checksum frame invalid')
    expected = min(size-offset, chunk_limit)
    wire = base64.b64decode(b''.join(encoded.split()), validate=True)
    if compressed:
        decoder = zlib.decompressobj(wbits=31)
        raw = decoder.decompress(wire, expected+1)
        if not decoder.eof or decoder.unused_data or len(raw) > expected:
            raise ValueError('invalid compressed collector frame')
    else:
        raw = wire
    if len(raw) != expected or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('collector length or checksum mismatch')
    return inode, size, raw


class SnapshotReader:
    """One temporary, sequenced reader; never a second live sampling worker."""
    def __init__(self, pod, filename, deadline, namespace='karthos-system'):
        from .collector_tail_stream import CollectorTailStream, reader_script
        self.deadline = deadline
        command = ['kubectl', 'exec', '-n', namespace, pod['metadata']['name'],
                   '-c', 'diag-reader', '-i', '--', 'sh', '-c', reader_script(filename, 4*1024*1024)]
        self.stream = CollectorTailStream(command, 8*1024*1024)
        self.recoveries = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.stream.close()

    def __call__(self, offset):
        for attempt in range(1, 4):
            requested = time.time_ns()
            remaining = self.deadline-time.monotonic()
            if remaining <= 0:
                raise TimeoutError('snapshot read deadline exhausted')
            try:
                response = self.stream.exchange(offset, min(10., remaining))
                received = time.time_ns()
                inode, size, raw = decode_frame(response, offset)
                return {'inode':inode, 'size':size, 'data':raw,
                        'requested_at_ns':requested, 'received_at_ns':received}
            except Exception as exc:
                self.recoveries.append({'attempt':attempt, 'offset':offset,
                                        'error_type':type(exc).__name__, 'reason':str(exc)})
                self.stream.close()
                if attempt == 3 or isinstance(exc, ValueError):
                    raise


def inventory_script(directory='/diag'):
    """Inventory every top-level entry; unknown types are visible, not followed."""
    import shlex
    quoted=shlex.quote(directory)
    return f'''set -eu
for p in {quoted}/* {quoted}/.[!.]* {quoted}/..?*; do
    [ -e "$p" ] || [ -L "$p" ] || continue
    if [ -f "$p" ] && [ ! -L "$p" ]; then
        printf 'file\\t%s\\n' "$p"
    else
        printf 'unsupported\\t%s\\n' "$p"
    fi
done'''
