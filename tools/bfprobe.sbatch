#!/usr/bin/env bash
#SBATCH -J bfprobe
#SBATCH -N 1
#SBATCH -c 8
#SBATCH --mem=16G
#SBATCH -t 01:00:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
for p in "$@"; do
  "$PY" "$REPO/tools/bfprobe.py" "$p" 6
  echo
done
