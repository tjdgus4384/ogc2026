#!/usr/bin/env bash
#SBATCH -J liftk
#SBATCH -N 1
#SBATCH -c 16
#SBATCH --mem=32G
#SBATCH -t 01:30:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
( "$PY" "$REPO/tools/liftk.py" solver prob_5 600 7 6 ) &
( "$PY" "$REPO/tools/liftk.py" solver prob_4 480 7 6 ) &
( "$PY" "$REPO/tools/liftk.py" solver prob_6 900 7 6 ) &
wait
