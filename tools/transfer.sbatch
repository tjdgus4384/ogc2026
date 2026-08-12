#!/bin/bash
#SBATCH -J transfer
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
# One 4-core slot per (instance, seed) so the anneals do not contend.
i=0
for p in prob_4 prob_5; do
  for s in 7 41 89; do
    c=$((i*4)); OMP_NUM_THREADS=4 taskset -c $c-$((c+3)) \
      $PY -u tools/transfer.py solver $p 180 $s > runs/transfer/${p}_s${s}.txt 2>&1 &
    i=$((i+1))
  done
done
wait
for p in prob_4 prob_5; do for s in 7 41 89; do echo "########## $p seed $s"; cat runs/transfer/${p}_s${s}.txt; done; done
