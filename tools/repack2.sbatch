#!/bin/bash
#SBATCH -J repack2
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/repack2
i=0
for arg in "prob_4 480 7" "prob_4 480 11" "prob_5 600 7" "prob_6 900 7"; do
  set -- $arg
  c=$((i*8)); OMP_NUM_THREADS=4 taskset -c $c-$((c+7)) \
    $PY -u tools/repack2.py $1 $2 $3 > runs/repack2/$1-s$3.txt 2>&1 &
  i=$((i+1))
done
wait
for f in runs/repack2/*.txt; do echo "##### $(basename $f)"; cat $f; done
