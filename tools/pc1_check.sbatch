#!/bin/bash
#SBATCH -J pc1chk
#SBATCH -N 1
#SBATCH -c 8
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/incconf
# Same probe, the patched kernel. The shipped model flags 5 pairs on a board its
# own vp passes with zero violations; if the tie rule was the cause this is 0.
OMP_NUM_THREADS=4 taskset -c 0-3 $PY -u tools/incumbent_conflict.py builds/pc1 prob_5 600 7 \
  2>&1 | tee runs/incconf/pc1_prob_5.txt
