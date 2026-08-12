#!/bin/bash
#SBATCH -J squeeze
#SBATCH -N 1
#SBATCH -c 4
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
for p in prob_4 prob_5 prob_6 prob_3; do $PY -u tools/squeeze.py $p runs/base53-s41/$p.sol.json; done
