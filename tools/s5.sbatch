#!/bin/bash
#SBATCH -J s5split
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -t 00:20:00
#SBATCH -o runs/s5-recovery-%j.log
set -uo pipefail
export PYTHONNOUSERSITE=1
PY="${OGC_PY312:-$PWD/.miniforge/envs/ogc2026/bin/python}"
OUT="${OGC_S5_OUT:-runs/s5-${SLURM_JOB_ID}}"
mkdir -p "$OUT"
i=0
pids=()
for arg in "prob_5 400 0.15 7" "prob_5 400 0.15 11" "prob_4 400 0.15 7" "prob_5 400 0.30 7"; do
  set -- $arg
  c=$((i*8)); OMP_NUM_THREADS=4 taskset -c $c-$((c+7)) \
    $PY -u tools/s5_split.py $1 $2 $3 $4 > "$OUT/$1-f$3-s$4.txt" 2>&1 &
  pids+=("$!")
  i=$((i+1))
done
rc=0
for pid in "${pids[@]}"; do
  wait "$pid" || rc=1
done
for f in "$OUT"/*.txt; do echo "##### $(basename $f)"; cat "$f"; done
exit "$rc"
