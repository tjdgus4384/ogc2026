#!/bin/bash
#SBATCH -J frag
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
for p in prob_4 prob_5; do
  echo "########## $p"
  $PY -u tools/frag45.py $p runs/base53-s41/$p.sol.json 3
done
