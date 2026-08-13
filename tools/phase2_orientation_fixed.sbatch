#!/usr/bin/env bash
#SBATCH -p titan
#SBATCH --exclusive
#SBATCH -c 32
#SBATCH -t 00:30:00
#SBATCH -J p2o-fixed
#SBATCH -o runs/phase2-orientation-fixed-%j.log
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

SOLVER="${1:-$ROOT/builds/phase2-oris}"
OUT="${2:-$ROOT/runs/phase2-orientation-fixed-${SLURM_JOB_ID}}"
ITERS="${3:-20000}"
SEEDS="${4:-7 11 23 41}"
mkdir -p "$OUT"
unset OGC_PHASE2_ORIS OGC_CAUSAL_BAY_RNG OGC_EXIT_TWIN \
  OGC_EXIT_TWIN_SHADOW OGC_EXIT_TWIN_POST

# The initial board is built once with the shipped OFF semantics.  Both arms
# load exactly these bytes, so the A/B measures only repair-path ordering.
pids=()
for instance in 3 4 5 6; do
  env -u OGC_PHASE2_ORIS OMP_NUM_THREADS=1 \
    "$PY" "$ROOT/tools/phase2_orientation_fixed.py" base \
    "$SOLVER" "$instance" "$OUT/p${instance}-base.json" &
  pids+=("$!")
done
status=0
for pid in "${pids[@]}"; do
  if ! wait "$pid"; then status=1; fi
done
if (( status != 0 )); then exit "$status"; fi

pids=()
core=0
for instance in 3 4 5 6; do
  for seed in $SEEDS; do
    (
      OMP_NUM_THREADS=1 taskset -c "$core" env -u OGC_PHASE2_ORIS \
        "$PY" "$ROOT/tools/phase2_orientation_fixed.py" run \
        "$SOLVER" "$instance" "$seed" "$ITERS" off \
        "$OUT/p${instance}-base.json" "$OUT/p${instance}-s${seed}-off.json"
      OMP_NUM_THREADS=1 taskset -c "$core" env OGC_PHASE2_ORIS=1 \
        "$PY" "$ROOT/tools/phase2_orientation_fixed.py" run \
        "$SOLVER" "$instance" "$seed" "$ITERS" on \
        "$OUT/p${instance}-base.json" "$OUT/p${instance}-s${seed}-on.json"
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
"$PY" "$ROOT/tools/phase2_orientation_fixed.py" collect "$OUT"
