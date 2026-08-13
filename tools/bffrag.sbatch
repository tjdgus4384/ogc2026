#!/usr/bin/env bash
#SBATCH -J bffrag
#SBATCH -N 1
#SBATCH -c 8
#SBATCH --mem=16G
#SBATCH -t 02:00:00
#
# Does bf1 actually reduce fragmentation? The gate measures the OBJECTIVE;
# this measures the MECHANISM, and the two answers together separate
# "the rule does not defragment" from "defragmenting does not pay".
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
for p in prob_5 prob_4; do
  for bf in 0 1; do
    echo "═══ $p  OGC_BESTFIT=$bf"
    OGC_BESTFIT=$bf "$PY" "$REPO/tools/blocked.py" "$REPO/builds/bf1" "$p" 600 7 || echo "  (failed)"
    echo
  done
done
