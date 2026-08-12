#!/bin/bash
#SBATCH -J shadow
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
for p in prob_3 prob_4 prob_5 prob_6; do $PY -u tools/shadow_cost.py $p; done
