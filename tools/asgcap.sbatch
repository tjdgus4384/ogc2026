#!/bin/bash
#SBATCH -J asgpin
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/asgcap
i=0
for r in 0.70 0.75; do
for s in 23 41 59 73; do
  c=$((i*4)); OMP_NUM_THREADS=4 taskset -c $c-$((c+3)) \
    $PY -u tools/asgcap.py solver prob_3 240 $r $s > runs/asgcap/pin-$r-s$s.txt 2>&1 &
  i=$((i+1))
done
done
wait
for f in runs/asgcap/pin-*.txt; do echo "##### $(basename $f)"; tail -3 $f; done
