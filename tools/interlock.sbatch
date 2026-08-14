#!/usr/bin/env bash
#SBATCH -J interlock
#SBATCH -N 1
#SBATCH -c 4
#SBATCH --mem=16G
#SBATCH -t 00:15:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
for a in "prob_4 480 7" "prob_5 600 7" "prob_6 900 7"; do
  OGC_SEED=7 "$PY" -u "$REPO/tools/interlock.py" $a || true
done
