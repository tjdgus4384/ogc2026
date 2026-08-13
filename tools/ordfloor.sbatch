#!/usr/bin/env bash
#SBATCH -J ordfloor
#SBATCH -N 1
#SBATCH -c 16
#SBATCH --mem=32G
#SBATCH -t 00:30:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
for a in "prob_5 600 7 11" "prob_5 600 13 17" "prob_4 480 7 11" "prob_3 240 7 11"; do
  ( OGC_SEED=$(echo $a|awk '{print $3}') "$PY" -u "$REPO/tools/ordfloor.py" $a ) &
done
wait
