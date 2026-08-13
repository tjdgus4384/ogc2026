#!/bin/bash
#SBATCH -p titan
#SBATCH --exclusive
#SBATCH -c 32
#SBATCH -t 00:20:00
#SBATCH -J jasg-stay
#SBATCH -o runs/voluntary-stay-rollout-%j.log
set -uo pipefail

ROOT="${OGC_ROOT:-$PWD}"
if [[ -n "${OGC_PY312:-}" ]]; then
  PY="$OGC_PY312"
elif [[ -x "$ROOT/.miniforge/envs/ogc2026/bin/python" ]]; then
  PY="$ROOT/.miniforge/envs/ogc2026/bin/python"
else
  COMMON_DIR="$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir)"
  CANONICAL_ROOT="$(dirname "$COMMON_DIR")"
  PY="$CANONICAL_ROOT/.miniforge/envs/ogc2026/bin/python"
fi
if [[ ! -x "$PY" ]]; then
  echo "Python 3.12 environment not found: $PY" >&2
  exit 2
fi

SOLVER="${1:-$ROOT/builds/jasg-stay-rollout}"
MANIFESTS="${2:-$ROOT/runs/joint-assignment-rollout-74933}"
OUT="${3:-$ROOT/runs/voluntary-stay-rollout-${SLURM_JOB_ID}}"
mkdir -p "$OUT"

pids=()
for problem in 3 4 5 6; do
  cpu=$(((problem - 3) * 8))
  taskset -c "$cpu-$((cpu + 7))" "$PY" \
    "$ROOT/tools/voluntary_stay_rollout.py" run \
    "$SOLVER" "$ROOT/data/preliminary_test/prob_${problem}.json" \
    "$MANIFESTS/prob_${problem}.json" "$OUT/prob_${problem}.json" &
  pids+=("$!")
done

status=0
for pid in "${pids[@]}"; do
  if ! wait "$pid"; then
    status=1
  fi
done
if [[ "$status" -ne 0 ]]; then
  exit "$status"
fi
"$PY" "$ROOT/tools/voluntary_stay_rollout.py" collect \
  "$OUT" "$OUT/summary.json"
