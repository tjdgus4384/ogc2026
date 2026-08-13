#!/usr/bin/env bash
#SBATCH -J ordmem
#SBATCH -N 1
#SBATCH -c 32
#SBATCH --mem=48G
#SBATCH -t 01:00:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
( OGC_SEED=7  "$PY" "$REPO/tools/ordmem.py" prob_5 600 7 11 120 ) &
( OGC_SEED=13 "$PY" "$REPO/tools/ordmem.py" prob_5 600 13 17 120 ) &
( OGC_SEED=7  "$PY" "$REPO/tools/ordmem.py" prob_4 480 7 11 120 ) &
( OGC_SEED=7  "$PY" "$REPO/tools/ordmem.py" prob_3 240 7 11 120 ) &
wait
