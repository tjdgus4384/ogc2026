#!/bin/bash
#SBATCH -J floor
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/floor
i=0
for p in prob_3 prob_4 prob_5 prob_6; do
 (for s in 7 11 23 41 59 73 89 101; do
    OMP_NUM_THREADS=4 taskset -c $((i*8))-$((i*8+7)) \
      $PY -u tools/floor.py solver $p $s
  done) > runs/floor/$p.txt 2>&1 &
  i=$((i+1))
done
wait
for p in prob_3 prob_4 prob_5 prob_6; do echo "##### $p"; cat runs/floor/$p.txt; done
