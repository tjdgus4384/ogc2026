#!/usr/bin/env bash
#SBATCH -J basin
#SBATCH -N 1
#SBATCH -c 32
#SBATCH --mem=48G
#SBATCH -t 03:00:00
#
# OGC_SEED must be in the environment BEFORE python starts -- myalgorithm reads
# _PSEED at import. Setting it inside the script silently did nothing and made
# two "seeds" the same run (08-13).
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
for s in 7 11 13 17; do
  ( OGC_SEED=$s "$PY" "$REPO/tools/basin.py" 30 1800 $s ) &
done
wait
