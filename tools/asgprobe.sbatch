#!/bin/bash
#SBATCH -J asgp
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/asgprobe
i=0
for s in 7 11 23 41; do
  c=$((i*8)); OMP_NUM_THREADS=4 taskset -c $c-$((c+7)) \
    $PY -u tools/asgprobe.py solver prob_3 160 $s > runs/asgprobe/s$s.txt 2>&1 &
  i=$((i+1))
done
wait
for s in 7 11 23 41; do echo "##### seed $s"; cat runs/asgprobe/s$s.txt; done
