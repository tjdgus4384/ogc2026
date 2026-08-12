#!/bin/bash
#SBATCH -J realize
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/realize
i=0
for r in 0.60 0.65 0.70 0.75; do
  c=$((i*8)); OMP_NUM_THREADS=4 taskset -c $c-$((c+7)) \
    $PY -u tools/realize.py prob_3 $r 120 > runs/realize/prob_3-$r.txt 2>&1 &
  i=$((i+1))
done
wait
for r in 0.60 0.65 0.70 0.75; do echo "##### rho $r"; cat runs/realize/prob_3-$r.txt; done
