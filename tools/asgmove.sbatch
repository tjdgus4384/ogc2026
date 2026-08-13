#!/usr/bin/env bash
#SBATCH -J asgmove
#SBATCH -N 1
#SBATCH -c 24
#SBATCH --mem=48G
#SBATCH -t 01:30:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
( OGC_SEED=7 "$PY" -u "$REPO/tools/asgmove.py" prob_3 240 120 ) &
( OGC_SEED=7 "$PY" -u "$REPO/tools/asgmove.py" prob_4 480 120 ) &
( OGC_SEED=7 "$PY" -u "$REPO/tools/asgmove.py" prob_5 600 120 ) &
( OGC_SEED=7 "$PY" -u "$REPO/tools/asgmove.py" prob_6 900 120 ) &
wait
