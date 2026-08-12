#!/bin/bash
#SBATCH -J opsdiag
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
# Operator-level diagnostics we have never looked at. The driver already logs
#   _diag("ops",    used=[...], imp=[...])   per-operator calls and improvements
#   _diag("repair", iters, failed, fail_pct) how often insertGreedy fails mid-anneal
# If some operators never improve, their share of the iteration budget is dead;
# if the repair failure rate is high, the search is spending itself on rejects.
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/opsdiag
i=0
for arg in "prob_4 480" "prob_5 600" "prob_6 900"; do
  set -- $arg
  c=$((i*8))
  ( OGC_DIAG=1 OGC_SEED=7 OMP_NUM_THREADS=4 taskset -c $c-$((c+7)) \
      $PY -u -c "
import json,sys
sys.path.insert(0,'solver')
import myalgorithm as ma
p=json.load(open('data/preliminary_test/$1.json'))
ma.algorithm(p, timelimit=$2)
" 2>&1 | grep -E '"diag": "(ops|repair|portfolio|slice|refine)"' ) > runs/opsdiag/$1.txt 2>&1 &
  i=$((i+1))
done
wait
for p in prob_4 prob_5 prob_6; do echo "##### $p"; cat runs/opsdiag/$p.txt; done
