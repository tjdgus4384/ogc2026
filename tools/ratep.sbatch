#!/bin/bash
#SBATCH -J ratep
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
# pc1 judged on its MECHANISM, not on the objective.
#
# The slice refinement is a two-armed bandit: anneal restarts vs CP-SAT exact
# re-placement, 30% of the budget. On P4 it reports rate_a = 956.9 per second
# and rate_p = 0.0 -- the CP arm earns NOTHING. Round 1 is always "p" and every
# fifth round explores it, so 2 of 6 rounds go to a dead arm: about 10% of the
# whole budget.
#
# rate_p = 0 is exactly what pc1's defect predicts. pairConflicts forbids
# same-entry-day pairs unless BOTH descent orders clear, so the CP model
# rejects the incumbent it was seeded with (measured: P5 5 pairs, P4 3 pairs,
# vp violations 0), must return something else, and anything worse is
# discarded -- the round is wasted by construction.
#
# So the honest test of pc1 is whether rate_p stops being zero. That is a
# mechanism metric with no lottery in it, unlike the 12-pair objective gate
# that returned p=0.37.
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/ratep
i=0
for arg in "prob_4 480" "prob_5 600" "prob_6 900"; do
  set -- $arg
  c=$((i*8))
  ( for d in solver builds/pc1; do
      for s in 7 11; do
        echo -n "$1 $d seed $s  "
        OGC_DIAG=1 OGC_SEED=$s OMP_NUM_THREADS=4 taskset -c $c-$((c+7)) \
          $PY -u -c "
import json,sys
sys.path.insert(0,'$d')
import myalgorithm as ma
p=json.load(open('data/preliminary_test/$1.json'))
ma.algorithm(p, timelimit=$2)
" 2>&1 | grep '"diag": "slice"' | tail -1
      done
    done ) > runs/ratep/$1.txt 2>&1 &
  i=$((i+1))
done
wait
for p in prob_4 prob_5 prob_6; do echo "##### $p"; cat runs/ratep/$p.txt; done
