#!/bin/bash
#SBATCH -J flchk3
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/floorcheck3
i=0
for arg in "solver prob_5 1" "builds/floor3 prob_5 2" "solver prob_6 1" "builds/floor3 prob_6 2"; do
  set -- $arg
  c=$((i*8)); OMP_NUM_THREADS=4 taskset -c $c-$((c+7)) \
    $PY -u tools/floorcheck3.py $1 $2 240 $3 > runs/floorcheck3/$(echo $1 | tr / -)-$2.txt 2>&1 &
  i=$((i+1))
done
wait
for f in runs/floorcheck3/*.txt; do cat $f; done
