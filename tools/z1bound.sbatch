#!/usr/bin/env bash
#SBATCH -J z1bound
#SBATCH -N 1
#SBATCH -c 4
#SBATCH --mem=8G
#SBATCH -t 00:30:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
"$REPO/.miniforge/envs/ogc2026/bin/python" "$REPO/tools/z1bound.py"
