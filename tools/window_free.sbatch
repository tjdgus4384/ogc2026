#!/bin/bash
#SBATCH -J winfree
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
for p in prob_5 prob_4 prob_6; do
  echo "########## $p"
  $PY -u tools/window_free.py $p runs/base53-s41/$p.sol.json 3
done
