#!/usr/bin/env bash
set -euo pipefail
export KARTHOS_REPO=/Users/kivel/.codex/worktrees/pilot-repairs/karthos
export KUBECONFIG=/Users/kivel/.kube/configs/remote-k3s.yaml
export KARTHOS_CLUSTER_PROFILE_OVERRIDE=remote-k3s
export KARTHOS_CLUSTER_PROFILE_FILE_OVERRIDE=/Users/kivel/Desktop/Projects/tcc/karthos/.env.d/remote-k3s.env
cd "$KARTHOS_REPO"
. "$KARTHOS_REPO/test/e2e/cluster/profile.sh"
cd /Users/kivel/Desktop/Projects/tcc/karthos-tcc-evaluation
export PYTHONPATH=/Users/kivel/Desktop/Projects/tcc/karthos-tcc-evaluation
export PYTHONDONTWRITEBYTECODE=1
exec python3 -P -m evaluation.timing_monitor "$@"
