#!/usr/bin/env bash
#SBATCH -J trivchk
#SBATCH -N 1
#SBATCH -c 4
#SBATCH --mem=16G
#SBATCH -t 00:20:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
"$REPO/.miniforge/envs/ogc2026/bin/python" "$REPO/tools/trivial_check.py" "$@"
