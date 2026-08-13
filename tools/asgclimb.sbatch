#!/usr/bin/env bash
#SBATCH -J asgclimb
#SBATCH -N 1
#SBATCH -c 16
#SBATCH --mem=32G
#SBATCH -t 01:00:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
( OGC_SEED=7 "$PY" -u "$REPO/tools/asgclimb.py" prob_3 240 120 420 ) &
( OGC_SEED=7 "$PY" -u "$REPO/tools/asgclimb.py" prob_4 480 120 420 ) &
( OGC_SEED=7 "$PY" -u "$REPO/tools/asgclimb.py" prob_5 600 120 420 ) &
wait
