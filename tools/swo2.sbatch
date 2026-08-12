#!/bin/bash
#SBATCH -J swo
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/swo2
i=0
for arg in "prob_6 80 7 400" "prob_5 60 7 400" "prob_4 60 7 400" "prob_6 80 11 400"; do
  set -- $arg
  c=$((i*8)); OMP_NUM_THREADS=4 taskset -c $c-$((c+7)) \
    $PY -u tools/swo2.py $1 $2 $3 $4 > runs/swo2/$1-s$3.txt 2>&1 &
  i=$((i+1))
done
wait
for f in runs/swo2/*.txt; do echo "##### $(basename $f)"; cat $f; done
