#!/usr/bin/env bash
#SBATCH -J schedx
#SBATCH -N 1
#SBATCH -c 16
#SBATCH --mem=32G
#SBATCH -t 00:40:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
( OGC_SEED=7 "$PY" "$REPO/tools/schedx.py" prob_5 600 7 11 ) &
( OGC_SEED=7 "$PY" "$REPO/tools/schedx.py" prob_4 480 7 11 ) &
( OGC_SEED=13 "$PY" "$REPO/tools/schedx.py" prob_5 600 13 17 ) &
wait
