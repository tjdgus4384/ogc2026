#!/bin/bash
#SBATCH -J resid
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/residual
i=0
for arg in "prob_3 240" "prob_6 900" "prob_4 480" "prob_5 600"; do
  set -- $arg
  c=$((i*8)); OMP_NUM_THREADS=4 taskset -c $c-$((c+7)) \
    $PY -u tools/incumbent_conflict.py builds/pc2 $1 $2 > runs/residual/$1-s$2.txt 2>&1 &
  i=$((i+1))
done
wait
for f in runs/residual/*.txt; do echo "##### $(basename $f)"; cat $f; done
