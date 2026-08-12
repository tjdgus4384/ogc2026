#!/bin/bash
#SBATCH -J jc2day
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/jc2day
i=0
for s in 7 11 23; do
  c=$((i*8)); OMP_NUM_THREADS=4 taskset -c $c-$((c+7)) \
    $PY -u tools/jointcon2.py prob_6 $s 400 > runs/jc2day/s$s.txt 2>&1 &
  i=$((i+1))
done
wait
for s in 7 11 23; do cat runs/jc2day/s$s.txt; done
