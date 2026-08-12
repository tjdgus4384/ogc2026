#!/bin/bash
#SBATCH -J asgcap
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/asgcap
i=0
for r in 0.65 0.70 0.75 0.80; do
  c=$((i*8)); OMP_NUM_THREADS=4 taskset -c $c-$((c+7)) \
    $PY -u tools/asgcap.py solver prob_3 240 $r 7 > runs/asgcap/rho$r.txt 2>&1 &
  i=$((i+1))
done
wait
for r in 0.65 0.70 0.75 0.80; do echo "##### rho $r"; cat runs/asgcap/rho$r.txt; done
