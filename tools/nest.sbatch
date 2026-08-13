#!/usr/bin/env bash
#SBATCH -J nest
#SBATCH -N 1
#SBATCH -c 8
#SBATCH --mem=32G
#SBATCH -t 00:20:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
OGC_SEED=7 timeout 600 "$PY" -u "$REPO/tools/nest.py" prob_4 480 7 0.5 3000 || true
