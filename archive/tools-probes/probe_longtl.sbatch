#!/usr/bin/env bash
#SBATCH --job-name=longtl
#SBATCH --nodes=1 --ntasks=1 --cpus-per-task=24 --mem=64G --time=00:50:00
#SBATCH --output=/data_fast/home/tjdgus4384/priv/ogc2026/runs/longtl.log
set -euo pipefail
ROOT=/data_fast/home/tjdgus4384/priv/ogc2026
PY=$ROOT/.miniforge/envs/ogc2026/bin/python
cd $ROOT
export OGC_SOLVER=$ROOT/solver OGC_DATA=preliminary_test OMP_NUM_THREADS=4
export OGC_SEED=7 OGC_DIAG=1 OGC_SAVE_SOL=1
mkdir -p runs/longtl
taskset -c 0-3  $PY tools/run_instance.py 4 2400 runs/longtl/prob_4.json > runs/longtl/prob_4.log 2>&1 &
taskset -c 4-7  $PY tools/run_instance.py 3 1200 runs/longtl/prob_3.json > runs/longtl/prob_3.log 2>&1 &
taskset -c 8-11 $PY tools/run_instance.py 5 2400 runs/longtl/prob_5.json > runs/longtl/prob_5.log 2>&1 &
wait
echo done > runs/longtl/DONE
