#!/usr/bin/env bash
#SBATCH -J craneck
#SBATCH -N 1
#SBATCH -c 4
#SBATCH --mem=16G
#SBATCH -t 00:20:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
for a in "prob_6 900 7" "prob_4 480 7" "prob_5 600 7"; do
  OGC_SEED=7 "$PY" -u "$REPO/tools/craneck.py" $a || true
done
