#!/bin/bash
#SBATCH -J asgcap
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/asgcap
i=0
for arg in "0.70 7" "0.75 7" "0.70 11" "0.75 11"; do
  set -- $arg
  c=$((i*8)); OMP_NUM_THREADS=4 taskset -c $c-$((c+7)) \
    $PY -u tools/asgcap.py solver prob_3 240 $1 $2 > runs/asgcap/pin-$1-s$2.txt 2>&1 &
  i=$((i+1))
done
wait
for f in runs/asgcap/pin-*.txt; do echo "##### $f"; cat $f; done
