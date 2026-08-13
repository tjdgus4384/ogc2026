#!/bin/bash
#SBATCH -p titan
#SBATCH --exclusive
#SBATCH -c 32
#SBATCH -t 00:10:00
#SBATCH -J jasg-roll
#SBATCH -o runs/joint-assignment-rollout-%j.log
set -uo pipefail

ROOT="${OGC_ROOT:-$PWD}"
PY="${OGC_PY312:-$ROOT/.miniforge/envs/ogc2026/bin/python}"
SOLVER="${1:-$ROOT/builds/pc2}"
OUT="${2:-$ROOT/runs/joint-assignment-rollout-${SLURM_JOB_ID}}"
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
