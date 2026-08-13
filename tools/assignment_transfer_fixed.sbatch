#!/usr/bin/env bash
#SBATCH -p titan
#SBATCH --exclusive
#SBATCH -c 32
#SBATCH -t 00:35:00
#SBATCH -J jasg-xfer
#SBATCH -o runs/assignment-transfer-%j.log
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
[[ -x "$PY" ]] || { echo "Python 3.12 environment not found: $PY" >&2; exit 2; }

SOLVER="${1:-$ROOT/builds/jasg-causal}"
MANIFEST="${2:-$ROOT/runs/joint-assignment-rollout-74933}"
OUT="${3:-$ROOT/runs/assignment-transfer-${SLURM_JOB_ID}}"
ITERS="${4:-20000}"
SEEDS="${5:-7 11 23 41}"
mkdir -p "$OUT"
unset OGC_PHASE2_ORIS OGC_EXIT_TWIN OGC_EXIT_TWIN_SHADOW OGC_EXIT_TWIN_POST
export OGC_CAUSAL_BAY_RNG=1

pids=()
core=0
for instance in 3 4 5 6; do
  for seed in $SEEDS; do
    (
      for mode in baseline start lock; do
        OMP_NUM_THREADS=1 taskset -c "$core" \
          "$PY" "$ROOT/tools/assignment_transfer_fixed.py" run \
          "$SOLVER" "$MANIFEST" "$instance" "$seed" "$ITERS" "$mode" \
          "$OUT/p${instance}-s${seed}-${mode}.json"
      done
    ) &
    pids+=("$!")
    core=$((core + 1))
  done
done

status=0
for pid in "${pids[@]}"; do
  if ! wait "$pid"; then status=1; fi
done
if (( status != 0 )); then exit "$status"; fi
"$PY" "$ROOT/tools/assignment_transfer_fixed.py" collect "$OUT"
