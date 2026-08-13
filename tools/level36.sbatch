#!/usr/bin/env bash
#SBATCH -J probe
#SBATCH -N 1
#SBATCH -c 8
#SBATCH --mem=32G
#SBATCH -t 00:30:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
"$REPO/.miniforge/envs/ogc2026/bin/python" -u "$REPO/tools/level.py" prob_3 prob_6
