#!/usr/bin/env bash
#SBATCH --job-name=probe
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=00:15:00
#SBATCH --output=/data_fast/home/tjdgus4384/priv/ogc2026/runs/prof-v4.log
#
# Where does anneal time go? insert vs scan p1/p2/p3 split on the two proxies
# (p27 z1-bound, p38 giant) -- the Amdahl input for the parallel-grain design.
set -euo pipefail

ROOT=/data_fast/home/tjdgus4384/priv/ogc2026
PY=$ROOT/.miniforge/envs/ogc2026/bin/python
export OGC_SOLVER=$ROOT/exp/v4spec
export SMAX_DEBUG=1

PY=$PY bash $ROOT/exp/v4spec/build.sh
for P in 27 38; do
    "$PY" $ROOT/tools/probe_vpthreads.py $P 1 20
done
echo "=== prof done ==="
