#!/usr/bin/env bash
#SBATCH -J rounds
#SBATCH -N 1
#SBATCH -c 20
#SBATCH --mem=48G
#SBATCH -t 02:30:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
( OGC_SEED=7 "$PY" -u "$REPO/tools/rounds.py" prob_5 200 4 ) &
( OGC_SEED=7 "$PY" -u "$REPO/tools/rounds.py" prob_4 200 4 ) &
( OGC_SEED=7 "$PY" -u "$REPO/tools/rounds.py" prob_6 240 4 ) &
( OGC_SEED=7 "$PY" -u "$REPO/tools/rounds.py" prob_3 120 4 ) &
wait
