#!/bin/bash
#SBATCH -J s5split
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -t 00:20:00
#SBATCH -o runs/s5-recovery-%j.log
export PYTHONNOUSERSITE=1
PY="${OGC_PY312:-$PWD/.miniforge/envs/ogc2026/bin/python}"
mkdir -p runs/s5
i=0
for arg in "prob_5 400 0.15 7" "prob_5 400 0.15 11" "prob_4 400 0.15 7" "prob_5 400 0.30 7"; do
  set -- $arg
  c=$((i*8)); OMP_NUM_THREADS=4 taskset -c $c-$((c+7)) \
    $PY -u tools/s5_split.py $1 $2 $3 $4 > runs/s5/$1-f$3-s$4.txt 2>&1 &
  i=$((i+1))
done
wait
for f in runs/s5/*.txt; do echo "##### $(basename $f)"; cat $f; done
