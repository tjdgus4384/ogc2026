#!/bin/bash
#SBATCH -J cycle
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
for p in prob_3 prob_4 prob_5 prob_6; do
  OMP_NUM_THREADS=4 taskset -c 0-3 $PY tools/cycle_probe.py solver $p 90 240 7
done
