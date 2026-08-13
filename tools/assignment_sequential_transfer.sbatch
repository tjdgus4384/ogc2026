#!/usr/bin/env bash
#SBATCH -p titan
#SBATCH --exclusive
#SBATCH -c 32
#SBATCH -t 00:15:00
#SBATCH -J jasg-seq-fi
#SBATCH -o runs/assignment-sequential-transfer-%j.log
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
CONSTRUCTIONS="${2:-$ROOT/results/assignment-sequential-74952}"
REFERENCE="${3:-$ROOT/runs/assignment-transfer-74943}"
OUT="${4:-$ROOT/runs/assignment-sequential-transfer-${SLURM_JOB_ID}}"
ITERS="${5:-20000}"
mkdir -p "$OUT"
unset OGC_PHASE2_ORIS OGC_EXIT_TWIN OGC_EXIT_TWIN_SHADOW OGC_EXIT_TWIN_POST \
  OGC_KEEP_ASSIGNMENT_LOCK
export OGC_CAUSAL_BAY_RNG=1

pids=()
core=0
for instance in 3 4 5 6; do
  for seed in 7 11 23 41; do
    OMP_NUM_THREADS=1 taskset -c "$core" \
      "$PY" "$ROOT/tools/assignment_sequential_transfer.py" run \
      "$SOLVER" "$CONSTRUCTIONS" "$instance" "$seed" "$ITERS" \
      "$OUT/p${instance}-s${seed}-sequential.json" &
    pids+=("$!")
    core=$((core + 1))
  done
done

status=0
for pid in "${pids[@]}"; do
  if ! wait "$pid"; then status=1; fi
done
if (( status != 0 )); then exit "$status"; fi
"$PY" "$ROOT/tools/assignment_sequential_transfer.py" collect \
  "$OUT" "$REFERENCE"
