#!/bin/bash
#SBATCH -J thspr
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
# Are the portfolio's four threads four DRAWS, or one draw computed four times?
#
# The kernel already answers it: runPortfolio returns each thread's final
# objective and the driver logs it as `thread_best`. If the four cluster
# tightly, best-of-4 buys nothing and the 10-12% per-draw spread we see across
# seeds lives entirely OUTSIDE the portfolio -- which would make thread
# diversity, not restart shape, the untouched half of the escape axis.
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/threadspread
i=0
for arg in "prob_4 480" "prob_5 600" "prob_6 900"; do
  set -- $arg
  c=$((i*8))
  ( for s in 7 11; do
      OGC_DIAG=1 OGC_SEED=$s OMP_NUM_THREADS=4 taskset -c $c-$((c+7)) \
        $PY -u -c "
import json,os,sys
sys.path.insert(0,'solver')
import myalgorithm as ma
p=json.load(open('data/preliminary_test/$1.json'))
ma.algorithm(p, timelimit=$2)
" 2>&1 | grep -E '"diag": "portfolio"' | sed "s/^/$1 seed $s  /"
    done ) > runs/threadspread/$1.txt 2>&1 &
  i=$((i+1))
done
wait
for p in prob_4 prob_5 prob_6; do echo "##### $p"; cat runs/threadspread/$p.txt; done
