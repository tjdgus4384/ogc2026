#!/bin/bash
#SBATCH -J reach45
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
for p in prob_4 prob_5; do
  echo "########## $p"
  OMP_NUM_THREADS=4 taskset -c 0-3 $PY tools/reach_probe.py solver $p runs/base53-s41/$p.sol.json 6
done
