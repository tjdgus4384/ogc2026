#!/bin/bash
#SBATCH -J flchk
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/floorcheck
i=0
for p in prob_3 prob_4 prob_5 prob_6; do
 (echo "=== shipped (expect _trivial / FAIL)"; \
  OMP_NUM_THREADS=4 taskset -c $((i*8))-$((i*8+7)) $PY -u tools/floorcheck.py solver $p 60; \
  echo "=== floor1 (expect PASS)"; \
  OMP_NUM_THREADS=4 taskset -c $((i*8))-$((i*8+7)) $PY -u tools/floorcheck.py builds/floor1 $p 60) \
  > runs/floorcheck/$p.txt 2>&1 &
  i=$((i+1))
done
wait
for p in prob_3 prob_4 prob_5 prob_6; do echo "########## $p"; cat runs/floorcheck/$p.txt; done
