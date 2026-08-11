#!/usr/bin/env bash
#SBATCH -J reach
#SBATCH -N 1
#SBATCH -c 4
#SBATCH --mem=32G
#SBATCH -t 02:00:00
#
#   sbatch -p titan tools/reach.sbatch prob_6 runs/base53-s41/prob_6.sol.json 4
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
OMP_NUM_THREADS=4 "$PY" "$REPO/tools/reach_probe.py" builds/m8 "$1" "$REPO/$2" "${3:-4}"
