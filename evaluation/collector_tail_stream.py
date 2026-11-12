"""One bounded, sequenced kubectl exec session per collector file."""
from __future__ import annotations

import os
import select
import subprocess
import time


def reader_script(filename, chunk_limit):
    if filename not in ('metrics.json', 'traces.json'):
        raise ValueError('unsafe diagnostic filename')
    return f'''set -eu
f=/diag/{filename}
read_chunk() (
    if [ ! -f "$f" ]; then printf 'missing\\n'; exit 0; fi
    for p in /diag/{filename[:-5]}*.json; do [ "$p" = "$f" ] || exit 40; done
    exec 3< "$f"
    meta=$(stat -Lc '%i:%s' /proc/self/fd/3) || exit 42
    size=${{meta#*:}}
    n=$((size-$1))
    [ "$n" -ge 0 ] || exit 41
    [ "$n" -le {chunk_limit} ] || n={chunk_limit}
    chunk=$(mktemp /tmp/evaluation-read.XXXXXX) || exit 42
    trap 'rm -f "$chunk"' EXIT HUP INT TERM
    dd if=/proc/self/fd/3 of="$chunk" bs=65536 iflag=skip_bytes,count_bytes skip="$1" count="$n" status=none || exit 42
    digest=$(sha256sum "$chunk") || exit 42
    digest=${{digest%% *}}
    printf '%s:%s\\n' "$meta" "$digest"
    gzip -1 -c "$chunk" | base64
)
while IFS=' ' read -r sequence offset; do
    case "$sequence" in ''|*[!0-9]*) exit 43 ;; esac
    printf 'begin:%s\\n' "$sequence"
    code=0
    case "$offset" in
        ready) printf 'ready\\n' ;;
        ''|*[!0-9]*) code=43 ;;
        *) read_chunk "$offset" || code=$? ;;
    esac
    printf '\\nend:%s:%s\\n' "$sequence" "$code"
done
'''


class CollectorTailStream:
    def __init__(self, command, max_frame_bytes):
        self.command = command
        self.max_frame_bytes = max_frame_bytes
        self.process = None
        self.sequence = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        process, self.process = self.process, None
        if process is None:
            return
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=1)
        finally:
            for pipe in (process.stdin, process.stdout, process.stderr):
                pipe.close()

    def handshake(self, timeout):
        response = self.exchange('ready', timeout)
        if response.returncode or response.stdout != b'ready\n':
            raise ValueError('collector stream handshake failed')

    def exchange(self, offset, timeout):
        """Return one response. A broken or partial frame forces reconnection."""
        deadline = time.monotonic() + timeout
        if self.process is None:
            self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            bufsize=0)
        process = self.process
        self.sequence += 1
        sequence = str(self.sequence).encode('ascii')
        start = b'begin:' + sequence + b'\n'
        end = b'\nend:' + sequence + b':'
        stdout, stderr = bytearray(), bytearray()
        pipes = [process.stdout, process.stderr]
        cursor = 0
        try:
            process.stdin.write(sequence+b' '+str(offset).encode('ascii')+b'\n')
            while True:
                remaining = deadline-time.monotonic()
                if remaining <= 0 or not (ready := select.select(pipes, [], [], remaining)[0]):
                    raise subprocess.TimeoutExpired(self.command[:2], timeout)
                for pipe in ready:
                    chunk = os.read(pipe.fileno(), 65536)
                    if not chunk:
                        pipes.remove(pipe)
                    if pipe is process.stderr:
                        stderr.extend(chunk)
                        if len(stderr) > 65536:
                            raise ValueError('collector stream stderr exceeded its bound')
                    elif not chunk:
                        raise ValueError('collector stream closed before a full response')
                    else:
                        stdout.extend(chunk)
                if len(stdout) > self.max_frame_bytes:
                    raise ValueError('collector stream frame exceeded its bound')
                if len(stdout) < len(start):
                    continue
                if not stdout.startswith(start):
                    raise ValueError('collector stream returned an unmatched response')
                marker = stdout.find(end, cursor)
                if marker == -1:
                    cursor = max(len(start), len(stdout)-len(end)+1)
                    continue
                code = stdout[marker+len(end):]
                if b'\n' not in code:
                    continue
                if not code.endswith(b'\n') or not code[:-1].isdigit():
                    raise ValueError('collector stream returned an invalid end frame')
                return subprocess.CompletedProcess(self.command[:2], int(code),
                                                   bytes(stdout[len(start):marker]), bytes(stderr))
        except Exception as exc:
            exc.output = bytes(stdout)
            exc.stderr = bytes(stderr)
            self.close()
            raise
