#!/bin/bash
#SBATCH -J realize3
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/realize3
i=0
for arg in "prob_4 480" "prob_5 600" "prob_6 900"; do
  set -- $arg
  c=$((i*8)); OMP_NUM_THREADS=4 taskset -c $c-$((c+7)) \
    $PY -u tools/realize3.py solver $1 $2 7 1.0 > runs/realize3/$1.txt 2>&1 &
  i=$((i+1))
done
wait
for p in prob_4 prob_5 prob_6; do echo "##### $p"; cat runs/realize3/$p.txt; done
