#!/usr/bin/env bash
#SBATCH --job-name=eval
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=24
#SBATCH --mem=96G
#SBATCH --time=00:25:00
#SBATCH --output=/dev/null
#
# The REAL preliminary P1-P6 (data/preliminary_test), each at its OFFICIAL
# time limit, six 4-core grading envelopes side by side on one node.
# Args: TAG SOLVER [PSEED] [ARMS]  (ARMS>0 -> OGC_ARMS depth split in v4spec+)
set -euo pipefail

ROOT=/data_fast/home/tjdgus4384/priv/ogc2026
PY=$ROOT/.miniforge/envs/ogc2026/bin/python

TAG="$1"; SOLVER="$2"; PSEED="${3:-7}"; ARMS="${4:-0}"

OUT=$ROOT/runs/$TAG
mkdir -p "$OUT"

export OGC_SOLVER="$SOLVER"
export OGC_DATA=preliminary_test
export OMP_NUM_THREADS=4
export OGC_DIAG=1
export OGC_SEED=$PSEED
export OGC_ARMS=$ARMS
export OGC_SAVE_SOL=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1

TLS=(60 120 240 480 600 900)   # official TLs, P1..P6 (preliminary report sec 2)

mapfile -t CPUS < <($PY -c "import os;print('\n'.join(map(str,sorted(os.sched_getaffinity(0)))))")

k=0
for i in 1 2 3 4 5 6; do
    span="${CPUS[$((k*4))]},${CPUS[$((k*4+1))]},${CPUS[$((k*4+2))]},${CPUS[$((k*4+3))]}"
    taskset -c "$span" "$PY" "$ROOT/tools/run_instance.py" "$i" "${TLS[$((i-1))]}" \
        "$OUT/prob_$i.json" > "$OUT/prob_$i.log" 2>&1 &
    k=$((k+1))
done
wait
echo done > "$OUT/DONE"
