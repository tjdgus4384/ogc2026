#!/usr/bin/env bash
#SBATCH -J asgclimb
#SBATCH -N 1
#SBATCH -c 8
#SBATCH --mem=24G
#SBATCH -t 00:30:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
OGC_SEED=7 "$REPO/.miniforge/envs/ogc2026/bin/python" -u "$REPO/tools/asgclimb.py" prob_3 240 120 900
