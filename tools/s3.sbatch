#!/bin/bash
#SBATCH -J s3beam
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/s3
i=0
for arg in "prob_4 7 400" "prob_5 7 400" "prob_6 7 400" "prob_4 11 400"; do
  set -- $arg
  c=$((i*8)); OMP_NUM_THREADS=4 taskset -c $c-$((c+7)) \
    $PY -u tools/s3_beam.py $1 $2 $3 > runs/s3/$1-s$2.txt 2>&1 &
  i=$((i+1))
done
wait
for f in runs/s3/*.txt; do echo "##### $(basename $f)"; cat $f; done
