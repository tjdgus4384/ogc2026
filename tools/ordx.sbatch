#!/usr/bin/env bash
#SBATCH -J ordx
#SBATCH -N 1
#SBATCH -c 16
#SBATCH --mem=32G
#SBATCH -t 00:45:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
( OGC_SEED=7  "$PY" "$REPO/tools/ordx.py" prob_5 600 7 11 ) &
( OGC_SEED=7  "$PY" "$REPO/tools/ordx.py" prob_4 480 7 11 ) &
( OGC_SEED=13 "$PY" "$REPO/tools/ordx.py" prob_5 600 13 17 ) &
( OGC_SEED=7  "$PY" "$REPO/tools/ordx.py" prob_3 240 7 11 ) &
wait
