#!/bin/bash
#SBATCH -J cmp2d
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/compact2d
i=0
for p in prob_4 prob_5 prob_6; do
  c=$((i*8)); OMP_NUM_THREADS=4 taskset -c $c-$((c+7)) \
    $PY -u tools/compact2d.py solver $p 600 7 > runs/compact2d/$p.txt 2>&1 &
  i=$((i+1))
done
wait
for p in prob_4 prob_5 prob_6; do echo "##### $p"; cat runs/compact2d/$p.txt; done
