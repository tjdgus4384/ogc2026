#!/bin/bash
#SBATCH -J richvoc
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/richvocab
i=0
# Same board, same holes, three vocabulary widths. 1 day = what the shipped CP
# arm sees; 6 and 20 days let the optimiser choose a LATER entry, which greedy
# can never express.
for d in 1 6 20; do
  c=$((i*8)); RICH_DAYS=$d RICH_CAND=60 OMP_NUM_THREADS=4 taskset -c $c-$((c+7)) \
    $PY -u tools/exactrepair.py solver prob_5 600 12 7 > runs/richvocab/days$d.txt 2>&1 &
  i=$((i+1))
done
wait
for d in 1 6 20; do echo "########## RICH_DAYS=$d"; cat runs/richvocab/days$d.txt; done
