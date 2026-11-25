#!/usr/bin/env bash
set -euo pipefail
EVAL_ROOT="$(cd "$(dirname "$0")" && pwd)"
KARTHOS_REPO="${KARTHOS_REPO:-$(cd "$EVAL_ROOT/.." && pwd)/karthos}"
export KARTHOS_REPO
case "${1:-}" in
  verify) cd "$EVAL_ROOT"; exec python3 -m evaluation.runner "$@" ;;
esac
cd "$KARTHOS_REPO"
# shellcheck disable=SC1091
. "$KARTHOS_REPO/test/e2e/cluster/profile.sh"
cd "$EVAL_ROOT"
if [[ "${1:-}" == "diagnose-artifacts" ]]; then
  shift
  exec python3 -m evaluation.artifact_diagnostic "$@"
fi
if [[ "${1:-}" == "run-repair-pilot" ]]; then
  shift
  exec python3 -m evaluation.repair_pilot "$@"
fi
if [[ "${1:-}" == "run-semantics-pilot" ]]; then
  shift
  exec python3 -m evaluation.semantics_pilot "$@"
fi
exec python3 -m evaluation.runner "$@"
