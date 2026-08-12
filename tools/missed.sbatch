#!/bin/bash
#SBATCH -J missed
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
for p in prob_4 prob_5 prob_6; do
  OMP_NUM_THREADS=1 taskset -c 0-3 $PY -u tools/missed.py solver $p 20 1
done
