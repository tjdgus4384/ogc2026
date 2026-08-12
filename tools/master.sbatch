#!/bin/bash
#SBATCH -J master
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/master
i=0
for p in prob_3 prob_4 prob_5 prob_6; do
  c=$((i*8)); taskset -c $c-$((c+7)) \
    $PY -u tools/master.py $p 0.55,0.60,0.65,0.70,0.75,0.80,0.90,1.00 \
    > runs/master/$p.txt 2>&1 &
  i=$((i+1))
done
wait
for p in prob_3 prob_4 prob_5 prob_6; do echo "##### $p"; cat runs/master/$p.txt; done
