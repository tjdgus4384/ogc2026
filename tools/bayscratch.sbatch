#!/bin/bash
#SBATCH -J bayscr
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/bayscratch
i=0
for b in 0 1 2 3; do
  c=$((i*8)); OMP_NUM_THREADS=4 taskset -c $c-$((c+7)) \
    $PY -u tools/bayscratch.py solver prob_5 600 $b > runs/bayscratch/bay$b.txt 2>&1 &
  i=$((i+1))
done
wait
for b in 0 1 2 3; do echo "########## bay $b"; cat runs/bayscratch/bay$b.txt; done
