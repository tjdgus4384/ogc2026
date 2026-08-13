#!/bin/bash
#SBATCH -p titan
#SBATCH --exclusive
#SBATCH -c 32
#SBATCH -t 00:10:00
#SBATCH -J jasg-roll
#SBATCH -o runs/joint-assignment-rollout-%j.log
set -uo pipefail

ROOT="${OGC_ROOT:-$PWD}"
if [[ -n "${OGC_PY312:-}" ]]; then
  PY="$OGC_PY312"
elif [[ -x "$ROOT/.miniforge/envs/ogc2026/bin/python" ]]; then
  PY="$ROOT/.miniforge/envs/ogc2026/bin/python"
else
  # Linked persistent worktrees intentionally do not duplicate the large
  # environment.  Resolve the canonical checkout through Git's common dir.
  COMMON_DIR="$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir)"
  CANONICAL_ROOT="$(dirname "$COMMON_DIR")"
  PY="$CANONICAL_ROOT/.miniforge/envs/ogc2026/bin/python"
fi
if [[ ! -x "$PY" ]]; then
  echo "Python 3.12 environment not found: $PY" >&2
  exit 2
fi
SOLVER="${1:-$ROOT/builds/pc2}"
OUT="${2:-$ROOT/runs/joint-assignment-rollout-${SLURM_JOB_ID}}"
export OGC_CAUSAL_BAY_RNG="${OGC_CAUSAL_BAY_RNG:-1}"
mkdir -p "$OUT"

pids=()
for problem in 3 4 5 6; do
  cpu=$(((problem - 3) * 8))
  taskset -c "$cpu-$((cpu + 7))" "$PY" \
    "$ROOT/tools/joint_assignment_rollout.py" \
    "$SOLVER" "$ROOT/data/preliminary_test/prob_${problem}.json" \
    "$OUT/prob_${problem}.json" &
  pids+=("$!")
done

status=0
for pid in "${pids[@]}"; do
  if ! wait "$pid"; then
    status=1
  fi
done
exit "$status"
