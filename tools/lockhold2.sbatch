#!/bin/bash
#SBATCH -J lockh2
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/lockhold2
i=0
for arg in "prob_3 60" "prob_4 60" "prob_6 60"; do
  set -- $arg
  c=$((i*8))
  ( echo "=== shipped"; OMP_NUM_THREADS=4 taskset -c $c-$((c+7)) $PY -u tools/lockhold.py solver $1 $2 prod
    echo "=== lock1";   OMP_NUM_THREADS=4 taskset -c $c-$((c+7)) $PY -u tools/lockhold.py builds/lock1 $1 $2 prod ) \
    > runs/lockhold2/$1.txt 2>&1 &
  i=$((i+1))
done
wait
for p in prob_3 prob_4 prob_6; do echo "##### $p"; cat runs/lockhold2/$p.txt; done
