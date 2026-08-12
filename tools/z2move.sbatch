#!/bin/bash
#SBATCH -J z2move
#SBATCH -N 1
#SBATCH -c 8
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/z2move
OMP_NUM_THREADS=4 taskset -c 0-3 $PY -u tools/z2move.py solver prob_3 240 7 2>&1 | tee runs/z2move/prob_3.txt
