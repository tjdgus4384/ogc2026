#!/usr/bin/env bash
#SBATCH -J asgbound
#SBATCH -N 1
#SBATCH -c 12
#SBATCH --mem=32G
#SBATCH -t 00:40:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
OGC_SEED=7 "$REPO/.miniforge/envs/ogc2026/bin/python" -u "$REPO/tools/asgbound.py"
