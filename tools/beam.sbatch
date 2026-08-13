#!/usr/bin/env bash
#SBATCH -J beam
#SBATCH -N 1
#SBATCH -c 8
#SBATCH --mem=24G
#SBATCH -t 01:00:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
for a in "prob_4 480 7 1 4" "prob_4 480 7 8 4" "prob_5 600 7 1 4" "prob_5 600 7 8 4"; do
  OGC_SEED=7 timeout 700 "$PY" -u "$REPO/tools/beam.py" $a || echo "   (timed out or failed: $a)"
done
