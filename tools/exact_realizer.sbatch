#!/bin/bash
#SBATCH -J exreal
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/exreal
# P4's three bays, instrument validation (board) then the real question
# (master), smallest bay first inside each arm.
i=0
for b in 1 0 2; do
 (for mode in board master; do
    OMP_NUM_THREADS=4 taskset -c $((i*8))-$((i*8+7)) \
      $PY -u tools/exact_realizer.py prob_4 $b $mode 480 2 7
  done) > runs/exreal/bay$b.txt 2>&1 &
  i=$((i+1))
done
wait
for b in 1 0 2; do echo "##### bay $b"; cat runs/exreal/bay$b.txt; done
