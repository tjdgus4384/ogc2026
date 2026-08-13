#!/bin/bash
#SBATCH --job-name=flex-time-audit
#SBATCH --partition=titan
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --time=00:08:00
#SBATCH --array=3-6%4
#SBATCH --output=runs/flexible-time-audit-%A_%a.out
#SBATCH --error=runs/flexible-time-audit-%A_%a.err

set -euo pipefail

ROOT="${OGC_ROOT:?set OGC_ROOT to the persistent worktree}"
CANONICAL_ROOT="/data_fast/home/tjdgus4384/priv/ogc2026"
PY="$CANONICAL_ROOT/.miniforge/envs/ogc2026/bin/python"
OUT="$ROOT/runs/flexible-time-audit-$SLURM_ARRAY_JOB_ID"
mkdir -p "$OUT"

exec "$PY" "$ROOT/tools/flexible_time_closure.py" \
  "$ROOT/data/preliminary_test/prob_$SLURM_ARRAY_TASK_ID.json" \
  "$ROOT/archive/best-boards-2026-08-04/prob_$SLURM_ARRAY_TASK_ID.sol.json" \
  --seconds "${OGC_FLEX_SECONDS:-60}" \
  --seed 1 \
  --checker-rounds 3 \
  --output-dir "$OUT"
