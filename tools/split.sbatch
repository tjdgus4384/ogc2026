#!/usr/bin/env bash
#SBATCH -J split
#SBATCH -N 1
#SBATCH -c 32
#SBATCH --mem=48G
#SBATCH -t 02:00:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
( OGC_SEED=7 "$PY" -u "$REPO/tools/split.py" prob_5 600 200 4 ) &
( OGC_SEED=7 "$PY" -u "$REPO/tools/split.py" prob_4 480 200 4 ) &
( OGC_SEED=7 "$PY" -u "$REPO/tools/split.py" prob_6 900 240 4 ) &
( OGC_SEED=7 "$PY" -u "$REPO/tools/split.py" prob_3 240 120 4 ) &
wait
