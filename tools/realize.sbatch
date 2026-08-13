#!/usr/bin/env bash
#SBATCH -J realize
#SBATCH -N 1
#SBATCH -c 8
#SBATCH --mem=24G
#SBATCH -t 00:30:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
for a in "prob_3 240 7" "prob_4 480 7" "prob_5 600 7" "prob_6 900 7" "prob_5 600 11"; do
  OGC_SEED=$(echo $a|awk '{print $3}') "$PY" -u "$REPO/tools/realize.py" $a || true
done
