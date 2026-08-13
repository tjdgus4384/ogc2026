#!/usr/bin/env bash
#SBATCH -J zsplit
#SBATCH -N 1
#SBATCH -c 4
#SBATCH --mem=16G
#SBATCH -t 00:15:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
OGC_SEED=7 "$REPO/.miniforge/envs/ogc2026/bin/python" -u "$REPO/tools/zsplit.py"
