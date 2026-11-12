"""POSIX shell wrapper publishing closed, checksummed generator artifacts.

READY denotes immutable files available for export, independently of workload
quality. ACK releases the container with its actual workload exit code; recovery
expiry returns 124 while retaining the workload exit code file and artifacts.
"""
from __future__ import annotations

import shlex


def artifact_command(generator: str, workload_command: str, version_command: str,
                     *, recovery_seconds: int = 1800) -> str:
    if generator not in ('k6', 'locust'):
        raise ValueError('unsupported generator')
    if not isinstance(recovery_seconds, int) or recovery_seconds <= 0:
        raise ValueError('recovery_seconds must be a positive integer')
    rename = ''
    if generator == 'locust':
        rename = """for part in stats failures exceptions stats_history; do
  if [ -f "$artifact_dir/locust_${part}.csv" ]; then
    mv "$artifact_dir/locust_${part}.csv" "$artifact_dir/locust-${part}.csv"
  fi
done"""
    workload = shlex.quote(workload_command)
    version = shlex.quote(version_command)
    guard = r'''max_seconds=${EVAL_WORKLOAD_MAX_SECONDS:-1800}
case "$max_seconds" in ''|*[!0-9]*) echo 'invalid workload budget' >&2; exit 70;; esac
seconds=$max_seconds
if [ -n "${EVAL_STUDY_DEADLINE_EPOCH:-}" ]; then
  case "$EVAL_STUDY_DEADLINE_EPOCH" in *[!0-9]*) echo 'invalid study deadline' >&2; exit 70;; esac
  # Reserve the one-second TERM-to-KILL grace so no active descendant survives
  # beyond the absolute study deadline (whole-second clock precision).
  remaining=$(( EVAL_STUDY_DEADLINE_EPOCH - $(date +%s) - 1 ))
  [ "$remaining" -ge "$seconds" ] || seconds=$remaining
fi
if [ "$seconds" -le 0 ]; then
  workload_rc=124
  printf 'workload skipped: study budget exhausted\n' >> "$artifact_dir/generator.log"
else
  command -v setsid >/dev/null || { echo 'setsid unavailable' >&2; exit 70; }
  watchdog_dir=$(mktemp -d "${TMPDIR:-/tmp}/eval-watchdog.XXXXXX")
  export EVAL_WATCHDOG_DIR="$watchdog_dir"
  watchdog_pid=''
  watchdog_cleaned=0
  cleanup_workload_group() {
    [ "$watchdog_cleaned" -eq 0 ] || return 0
    watchdog_cleaned=1
    : > "$watchdog_dir/completed"
    if [ -n "$watchdog_pid" ]; then
      kill -TERM "$watchdog_pid" 2>/dev/null || :
      wait "$watchdog_pid" 2>/dev/null || :
    fi
    if [ -s "$watchdog_dir/group" ]; then
      group=$(cat "$watchdog_dir/group")
      if kill -0 "-$group" 2>/dev/null; then
        kill -TERM "-$group" 2>/dev/null || :
        sleep 1
        kill -KILL "-$group" 2>/dev/null || :
      fi
    fi
  }
  trap 'cleanup_workload_group; rm -rf "$watchdog_dir"' EXIT
  trap 'cleanup_workload_group; exit 143' TERM
  trap 'cleanup_workload_group; exit 130' INT
  setsid /bin/sh -c 'printf "%s\n" "$$" > "$EVAL_WATCHDOG_DIR/group"; [ ! -f "$EVAL_WATCHDOG_DIR/completed" ] || exit 143; exec /bin/sh -c "$1"' wrapper __WORKLOAD__ >> "$artifact_dir/generator.log" 2>&1 &
  workload_pid=$!
  (
    sleeper=''
    trap 'if [ -n "$sleeper" ]; then kill "$sleeper" 2>/dev/null || :; wait "$sleeper" 2>/dev/null || :; fi; exit 0' TERM INT
    sleep "$seconds" &
    sleeper=$!
    wait "$sleeper" || exit 0
    sleeper=''
    [ ! -f "$watchdog_dir/completed" ] || exit 0
    [ -s "$watchdog_dir/group" ] || exit 0
    group=$(cat "$watchdog_dir/group")
    [ ! -f "$watchdog_dir/completed" ] || exit 0
    : > "$watchdog_dir/timed-out"
    kill -TERM "-$group" 2>/dev/null || :
    sleep 1 &
    sleeper=$!
    wait "$sleeper" || exit 0
    sleeper=''
    [ ! -f "$watchdog_dir/completed" ] || exit 0
    kill -KILL "-$group" 2>/dev/null || :
  ) &
  watchdog_pid=$!
  if wait "$workload_pid"; then
    workload_rc=0
  else
    workload_rc=$?
  fi
  if [ -f "$watchdog_dir/timed-out" ]; then workload_rc=124; fi
  cleanup_workload_group
  rm -rf "$watchdog_dir"
  trap - EXIT TERM INT
fi
'''.replace('__WORKLOAD__', workload)
    return f'''set -eu
artifact_dir=${{EVAL_ARTIFACT_DIR:-/tmp/eval-artifacts}}
export EVAL_ARTIFACT_DIR="$artifact_dir"
recovery=${{EVAL_RECOVERY_SECONDS:-{recovery_seconds}}}
case "$recovery" in ''|*[!0-9]*) echo 'invalid recovery seconds' >&2; exit 70;; esac
[ "$recovery" -gt 0 ] || exit 70
: "${{RUN_ID:?RUN_ID must be set}}"
# Every Job starts with an empty directory. Reject preexisting state rather than
# publishing stale outputs from a prior workload under a new run identifier.
mkdir -p "$artifact_dir"
for existing in "$artifact_dir"/* "$artifact_dir"/.[!.]* "$artifact_dir"/..?*; do
  [ ! -e "$existing" ] || {{ echo 'artifact directory is not empty' >&2; exit 70; }}
done
: > "$artifact_dir/generator.log"
printf '%s\\n' "$RUN_ID" > "$artifact_dir/run-id.txt"
if /bin/sh -c {version} > "$artifact_dir/generator-version.txt" 2>&1; then
  :
else
  version_rc=$?
  version_reason=$(head -n 1 "$artifact_dir/generator-version.txt")
  printf 'EVAL_ARTIFACT_INIT_ERROR version command failed (exit %s): %s\\n' "$version_rc" "$version_reason" >&2
  exit 70
fi
date -u '+%Y-%m-%dT%H:%M:%SZ' > "$artifact_dir/workload-started-at.txt"
{guard}
date -u '+%Y-%m-%dT%H:%M:%SZ' > "$artifact_dir/workload-ended-at.txt"
printf '%s\\n' "$workload_rc" > "$artifact_dir/workload-exit-code.txt"
{rename}
# All generator outputs are now closed. SHA256SUMS and READY become visible
# atomically; neither READY nor the later ACK belongs to the immutable manifest.
(
  cd "$artifact_dir"
  for artifact in *; do
    [ -f "$artifact" ] || continue
    sha256sum "$artifact" || exit 70
  done > .SHA256SUMS.tmp
  mv .SHA256SUMS.tmp SHA256SUMS
  printf '%s\\n' ready > .READY.tmp
  mv .READY.tmp READY
)
printf 'EVAL_ARTIFACT_READY %s\\n' "$RUN_ID"
deadline=$(( $(date +%s) + recovery ))
while [ ! -f "$artifact_dir/ACK" ]; do
  if [ "$(date +%s)" -ge "$deadline" ]; then
    printf 'EVAL_ARTIFACT_RECOVERY_TIMEOUT %s\\n' "$RUN_ID" >&2
    exit 124
  fi
  sleep 1
done
exit "$workload_rc"
'''
