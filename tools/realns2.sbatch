#!/usr/bin/env bash
#SBATCH -J realns2
#SBATCH -N 1
#SBATCH -c 8
#SBATCH --mem=24G
#SBATCH -t 00:45:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
for pid in "prob_4 480" "prob_5 600" "prob_6 900"; do
  echo "=== $pid corner-only ==="
  OGC_SLIDE=0 OGC_SEED=7 "$PY" -u "$REPO/tools/realns.py" $pid 7 150 || true
  echo "=== $pid corner+slide ==="
  OGC_SLIDE=1 OGC_SEED=7 "$PY" -u "$REPO/tools/realns.py" $pid 7 150 || true
done
