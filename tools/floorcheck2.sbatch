#!/bin/bash
#SBATCH -J flchk2
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/floorcheck2
i=0
for p in prob_3 prob_4 prob_5 prob_6; do
 (for d in solver builds/floor1 builds/floor2; do
    echo "=== $d"
    OMP_NUM_THREADS=4 taskset -c $((i*8))-$((i*8+7)) $PY -u tools/floorcheck.py $d $p 60
  done) > runs/floorcheck2/$p.txt 2>&1 &
  i=$((i+1))
done
wait
for p in prob_3 prob_4 prob_5 prob_6; do echo "########## $p"; cat runs/floorcheck2/$p.txt; done
