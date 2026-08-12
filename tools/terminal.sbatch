#!/bin/bash
#SBATCH -J terminal
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/terminal
i=0
for p in prob_4 prob_5 prob_6; do
  c=$((i*4)); OMP_NUM_THREADS=4 taskset -c $c-$((c+3)) \
    $PY -u tools/terminal.py solver $p 240 400 7 > runs/terminal/${p}.txt 2>&1 &
  i=$((i+1))
done
wait
for p in prob_4 prob_5 prob_6; do echo "########## $p"; cat runs/terminal/${p}.txt; done
