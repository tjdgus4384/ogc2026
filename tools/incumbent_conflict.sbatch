#!/bin/bash
#SBATCH -J incconf
#SBATCH -N 1
#SBATCH -c 8
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/incconf
for p in prob_5 prob_4; do
  OMP_NUM_THREADS=4 taskset -c 0-3 $PY -u tools/incumbent_conflict.py solver $p 600 7 \
    2>&1 | tee runs/incconf/$p.txt
done
