#!/bin/bash
#SBATCH -J delay
#SBATCH -N 1
#SBATCH -c 8
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
for p in prob_6 prob_5 prob_4; do
  echo "########## $p"
  OMP_NUM_THREADS=4 taskset -c 0-3 $PY -u tools/delay_probe.py solver $p runs/base53-s41/$p.sol.json 5
done
