#!/usr/bin/env bash
#SBATCH -J warm
#SBATCH -N 1
#SBATCH -c 32
#SBATCH --mem=48G
#SBATCH -t 02:00:00
#
# Precompute every board the structural probes need, once. After this a probe
# costs seconds instead of the 480-900s it spent re-solving the same instance.
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
for s in 7 11 13 17; do
  ( OGC_SEED=$s "$PY" "$REPO/tools/board.py" warm prob_3 240 $s ) &
  ( OGC_SEED=$s "$PY" "$REPO/tools/board.py" warm prob_4 480 $s ) &
  ( OGC_SEED=$s "$PY" "$REPO/tools/board.py" warm prob_5 600 $s ) &
  ( OGC_SEED=$s "$PY" "$REPO/tools/board.py" warm prob_6 900 $s ) &
done
wait
