#!/bin/bash
#SBATCH -J exactrep
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/exactrepair
i=0
for spec in "prob_4 480" "prob_5 600"; do
  set -- $spec
  c=$((i*8)); OMP_NUM_THREADS=4 taskset -c $c-$((c+7)) \
    $PY -u tools/exactrepair.py solver $1 $2 20 7 > runs/exactrepair/$1.txt 2>&1 &
  i=$((i+1))
done
wait
for p in prob_4 prob_5; do echo "########## $p"; cat runs/exactrepair/${p}.txt; done
