#!/usr/bin/env bash
#SBATCH -J share
#SBATCH -N 1
#SBATCH -c 16
#SBATCH --mem=32G
#SBATCH -t 03:00:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
OGC_SHARES=0.30,0.45,0.60 "$REPO/.miniforge/envs/ogc2026/bin/python" -u "$REPO/tools/share.py" prob_6 900 3
