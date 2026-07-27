#!/usr/bin/env bash
#SBATCH --job-name=ogc-eval
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=40
#SBATCH --mem=160G
#SBATCH --output=/dev/null
#
# One whole node = 10 instance slots of 4 pinned cores + 16 GB each -- ten
# copies of the grading envelope side by side. A full 40 is 4 of these jobs.
# Submit through tools/submit_eval.sh; args: TAG TL SOLVER "i1 i2 ... i10".
set -euo pipefail

ROOT=/data_fast/home/tjdgus4384/priv/ogc2026
PY=$ROOT/.miniforge/envs/ogc2026/bin/python

TAG="$1"; TL="$2"; SOLVER="$3"; INSTS="$4"

OUT=$ROOT/runs/$TAG
mkdir -p "$OUT"

export OGC_SOLVER="$SOLVER"
export OMP_NUM_THREADS=4
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1

# Pin each slot to 4 of the cores Slurm actually gave us (absolute ids differ
# per node, so read the cgroup mask instead of assuming 0..39).
mapfile -t CPUS < <($PY -c "import os;print('\n'.join(map(str,sorted(os.sched_getaffinity(0)))))")

k=0
for i in $INSTS; do
    span="${CPUS[$((k*4))]},${CPUS[$((k*4+1))]},${CPUS[$((k*4+2))]},${CPUS[$((k*4+3))]}"
    taskset -c "$span" "$PY" "$ROOT/tools/run_instance.py" "$i" "$TL" \
        "$OUT/prob_$i.json" > "$OUT/prob_$i.log" 2>&1 &
    k=$((k+1))
done
wait
