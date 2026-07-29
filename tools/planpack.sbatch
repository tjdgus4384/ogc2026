#!/usr/bin/env bash
#SBATCH --job-name=probe
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=00:40:00
#SBATCH --output=/data_fast/home/tjdgus4384/priv/ogc2026/runs/planpack.log
#
# The Leap-C gate experiment: generate the CP area-relaxation Z1=0 plan for
# each real board, then measure how much of it the geometric packer realises.
set -euo pipefail

ROOT=/data_fast/home/tjdgus4384/priv/ogc2026
PY=$ROOT/.miniforge/envs/ogc2026/bin/python
export OGC_SOLVER=$ROOT/exp/v8win

PY=$PY bash $ROOT/exp/v8win/build.sh

for P in 4 5 6; do
    echo "=== P$P: CP plan ==="
    "$PY" $ROOT/tools/z1bound.py $ROOT/data/preliminary_test/prob_$P.json 300 \
        $ROOT/runs/plan_p$P.json
    echo "=== P$P: chase the plan ==="
    "$PY" $ROOT/tools/plan_pack.py $ROOT/data/preliminary_test/prob_$P.json \
        $ROOT/runs/plan_p$P.json
done
echo "=== planpack done ==="
