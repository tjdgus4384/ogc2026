#!/usr/bin/env bash
#SBATCH -J lockdown
#SBATCH -N 1
#SBATCH -c 12
#SBATCH --mem=32G
#SBATCH -t 02:00:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
OGC_LOCKQ=0.4,0.0,0.15,0.28 OGC_SEED=7 "$PY" -u "$REPO/tools/lockq.py" prob_3 220 5
