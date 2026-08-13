#!/usr/bin/env bash
#SBATCH -J burstk
#SBATCH -N 1
#SBATCH -c 12
#SBATCH --mem=32G
#SBATCH -t 02:30:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
OGC_BURSTK=8,12,16 OGC_SEED=7 "$PY" -u "$REPO/tools/burstk.py" prob_3 220 5
