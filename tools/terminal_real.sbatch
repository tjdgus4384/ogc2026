#!/bin/bash
#SBATCH -J termreal
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/terminal
i=0
for spec in "prob_4 -480" "prob_5 -600" "prob_3 -240"; do
  set -- $spec
  c=$((i*4)); OMP_NUM_THREADS=4 taskset -c $c-$((c+3)) \
    $PY -u tools/terminal.py solver $1 $2 400 7 > runs/terminal/real_$1.txt 2>&1 &
  i=$((i+1))
done
wait
for p in prob_3 prob_4 prob_5; do echo "########## $p"; cat runs/terminal/real_${p}.txt; done
