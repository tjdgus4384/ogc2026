#!/usr/bin/env bash
#SBATCH -J tslice
#SBATCH -N 1
#SBATCH -c 16
#SBATCH --mem=32G
#SBATCH -t 02:00:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
( "$PY" "$REPO/tools/tslice.py" prob_5 600 7 ) &
( "$PY" "$REPO/tools/tslice.py" prob_4 480 7 ) &
( "$PY" "$REPO/tools/tslice.py" prob_6 900 7 ) &
wait
