#!/bin/sh
set -eu
EVALUATION_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$EVALUATION_ROOT"
export PYTHONDONTWRITEBYTECODE=1
exec python3 -B -m evaluation.gke "$@"
