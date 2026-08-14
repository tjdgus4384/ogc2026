#!/usr/bin/env bash
#SBATCH -J candreplay
#SBATCH -N 1
#SBATCH -c 8
#SBATCH --mem=32G
#SBATCH -t 02:00:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
OGC_SEED=7 "$PY" -u "$REPO/tools/candreplay.py" prob_6 900 7 120
OGC_SEED=7 "$PY" -u "$REPO/tools/candreplay.py" prob_4 480 7 120
