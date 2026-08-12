#!/bin/bash
#SBATCH -J lockh
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/lockhold
i=0
for arg in "prob_3 224 prod" "prob_3 224 cp" "prob_4 300 prod" "prob_6 400 prod"; do
  set -- $arg
  c=$((i*8)); OMP_NUM_THREADS=4 taskset -c $c-$((c+7)) \
    $PY -u tools/lockhold.py solver $1 $2 $3 > runs/lockhold/$1-$3-$2.txt 2>&1 &
  i=$((i+1))
done
wait
for f in runs/lockhold/*.txt; do echo "##### $f"; cat $f; done
