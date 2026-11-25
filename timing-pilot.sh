#!/usr/bin/env bash
set -euo pipefail
EVAL_ROOT="$(cd "$(dirname "$0")" && pwd)"
export KARTHOS_REPO="${KARTHOS_REPO:-$(cd "$EVAL_ROOT/.." && pwd)/karthos}"
: "${KUBECONFIG:?set KUBECONFIG to the remote k3s cluster configuration}"
export KUBECONFIG
export KARTHOS_CLUSTER_PROFILE_OVERRIDE=remote-k3s
export KARTHOS_CLUSTER_PROFILE_FILE_OVERRIDE="${KARTHOS_CLUSTER_PROFILE_FILE_OVERRIDE:-$KARTHOS_REPO/.env.d/remote-k3s.env}"
cd "$KARTHOS_REPO"
. "$KARTHOS_REPO/test/e2e/cluster/profile.sh"
cd "$EVAL_ROOT"
export PYTHONPATH="$EVAL_ROOT"
export PYTHONDONTWRITEBYTECODE=1
exec python3 -P -m evaluation.timing_monitor "$@"
