#!/usr/bin/env bash
#SBATCH -J ordsrch
#SBATCH -N 1
#SBATCH -c 4
#SBATCH --mem=32G
#SBATCH -t 00:40:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
OMP_NUM_THREADS=4 "$PY" "$REPO/tools/order_search.py" builds/m8 "$1" "$2" "${3:-7}"
