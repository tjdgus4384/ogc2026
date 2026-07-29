#!/usr/bin/env bash
#SBATCH --job-name=probe
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=32G
#SBATCH --time=00:40:00
#SBATCH --output=/data_fast/home/tjdgus4384/priv/ogc2026/runs/z1bound.log
#
# CP-SAT area-relaxation Z1 lower bounds for the real P3-P6 (300s each,
# 4 workers apiece, run concurrently).
set -euo pipefail

ROOT=/data_fast/home/tjdgus4384/priv/ogc2026
PY=$ROOT/.miniforge/envs/ogc2026/bin/python

for P in 3 4 5 6; do
    "$PY" $ROOT/tools/z1bound.py $ROOT/data/preliminary_test/prob_$P.json 300 &
done
wait
echo "=== z1bound done ==="
