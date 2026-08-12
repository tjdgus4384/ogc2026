#!/bin/bash
#SBATCH -J realize2
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/realize2
i=0
for arg in "prob_4 480" "prob_5 600" "prob_6 900"; do
  set -- $arg
  c=$((i*8)); OMP_NUM_THREADS=4 taskset -c $c-$((c+7)) \
    $PY -u tools/realize2.py solver $1 $2 7 1.0 > runs/realize2/$1.txt 2>&1 &
  i=$((i+1))
done
wait
for p in prob_4 prob_5 prob_6; do echo "##### $p"; cat runs/realize2/$p.txt; done
