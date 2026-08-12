#!/bin/bash
#SBATCH -J resid
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/residual
i=0
for arg in "prob_4 11" "prob_5 7" "prob_6 7" "prob_3 7"; do
  set -- $arg
  c=$((i*8)); OGC_SEED=$2 OMP_NUM_THREADS=4 taskset -c $c-$((c+7)) \
    $PY -u tools/incumbent_conflict.py builds/v62pc1 $1 240 > runs/residual/$1-s$2.txt 2>&1 &
  i=$((i+1))
done
wait
for f in runs/residual/*.txt; do echo "##### $(basename $f)"; cat $f; done
