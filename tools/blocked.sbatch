#!/bin/bash
#SBATCH -J blocked
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/blocked
i=0
for p in prob_4 prob_5 prob_6; do
  c=$((i*8)); OMP_NUM_THREADS=4 taskset -c $c-$((c+7)) \
    $PY -u tools/blocked.py solver $p 600 7 > runs/blocked/$p.txt 2>&1 &
  i=$((i+1))
done
wait
for p in prob_4 prob_5 prob_6; do echo "##### $p"; cat runs/blocked/$p.txt; done
