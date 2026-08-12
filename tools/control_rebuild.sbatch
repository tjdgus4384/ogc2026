#!/bin/bash
#SBATCH -J ctrl
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/control
i=0
for arg in "prob_4 480" "prob_5 600" "prob_6 900"; do
  set -- $arg
  c=$((i*8)); OMP_NUM_THREADS=4 taskset -c $c-$((c+7)) \
    $PY -u tools/control_rebuild.py solver $1 $2 7 > runs/control/$1.txt 2>&1 &
  i=$((i+1))
done
wait
for p in prob_4 prob_5 prob_6; do echo "##### $p"; cat runs/control/$p.txt; done
