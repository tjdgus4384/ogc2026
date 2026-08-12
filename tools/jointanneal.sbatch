#!/bin/bash
#SBATCH -J jointann
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/jointanneal
i=0
for arg in "prob_6 400 7" "prob_6 400 11" "prob_6 400 23" "prob_5 300 7"; do
  set -- $arg
  c=$((i*8)); OMP_NUM_THREADS=4 taskset -c $c-$((c+7)) \
    $PY -u tools/jointanneal.py $1 $2 $3 > runs/jointanneal/$1-s$3.txt 2>&1 &
  i=$((i+1))
done
wait
for f in runs/jointanneal/*.txt; do echo "##### $(basename $f)"; cat $f; done
