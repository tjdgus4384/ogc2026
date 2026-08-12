#!/bin/bash
#SBATCH -J repack
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/repack
i=0
for b in 0 1 2; do
  c=$((i*8)); OMP_NUM_THREADS=4 taskset -c $c-$((c+7)) \
    $PY -u tools/repack.py prob_4 $b 480 7 > runs/repack/bay$b.txt 2>&1 &
  i=$((i+1))
done
wait
for b in 0 1 2; do echo "##### bay $b"; cat runs/repack/bay$b.txt; done
