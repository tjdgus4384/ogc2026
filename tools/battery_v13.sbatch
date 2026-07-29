#!/usr/bin/env bash
#SBATCH --job-name=build
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=00:20:00
#SBATCH --output=/data_fast/home/tjdgus4384/priv/ogc2026/runs/battery-v13.log
#
# exp/v13uni battery after diagnostic-infra fold: canaries MUST stay byte-exact (all additions are gated/inert)
# 
set -euo pipefail

ROOT=/data_fast/home/tjdgus4384/priv/ogc2026
PY=$ROOT/.miniforge/envs/ogc2026/bin/python
export OGC_SOLVER=$ROOT/exp/v13uni

echo "=== (0) build ==="
PY=$PY bash $ROOT/exp/v13uni/build.sh

echo "=== (1) canaries (MUST equal v2.1: p1 1499, p2 3690) ==="
for P in 1 2; do
    "$PY" $ROOT/tools/run_instance.py $P 20
done
echo "=== battery done ==="
