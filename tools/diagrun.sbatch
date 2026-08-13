#!/usr/bin/env bash
#SBATCH -J probe
#SBATCH -N 1
#SBATCH -c 8
#SBATCH --mem=32G
#SBATCH -t 00:40:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
OGC_SEED=7 OGC_DIAG=1 "$PY" -u "$REPO/tools/run_instance.py" "$1" "$2"
