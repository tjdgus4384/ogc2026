#!/usr/bin/env bash
#SBATCH --job-name=build
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=00:20:00
#SBATCH --output=/data_fast/home/tjdgus4384/priv/ogc2026/runs/battery-solver.log
#
# Build + canary battery for any solver dir. Arg 1 (default: solver).
# Canary p1 1499 / p2 3690 are the v2.1-lineage fixed points; a trajectory-
# changing candidate reports different values -- read, don't assert.
# 
set -euo pipefail

ROOT=/data_fast/home/tjdgus4384/priv/ogc2026
PY=$ROOT/.miniforge/envs/ogc2026/bin/python
export OGC_SOLVER=$ROOT/${1:-solver}

echo "=== (0) build ==="
PY=$PY bash $OGC_SOLVER/build.sh

echo "=== (1) canaries (MUST equal v2.1: p1 1499, p2 3690) ==="
for P in 1 2; do
    "$PY" $ROOT/tools/run_instance.py $P 20
done
echo "=== battery done ==="
