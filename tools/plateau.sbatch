#!/usr/bin/env bash
#SBATCH --job-name=probe
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=12
#SBATCH --mem=48G
#SBATCH --time=00:15:00
#SBATCH --output=/dev/null
#
# Convergence plateau probe: v2.1 on real P4/P5/P6 at HALF the official TL.
# If half-TL quality ~= full-TL, independent restart-max harvests the draw
# spread (sigma ~6.5% on P4) nearly for free. Args: TAG PSEED
set -euo pipefail

ROOT=/data_fast/home/tjdgus4384/priv/ogc2026
PY=$ROOT/.miniforge/envs/ogc2026/bin/python
TAG="$1"; PSEED="${2:-7}"
OUT=$ROOT/runs/$TAG
mkdir -p "$OUT"

export OGC_SOLVER=$ROOT/solver
export OGC_DATA=preliminary_test
export OMP_NUM_THREADS=4
export OGC_SEED=$PSEED
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1

TLS=(240 300 450)
INSTS=(4 5 6)

mapfile -t CPUS < <($PY -c "import os;print('\n'.join(map(str,sorted(os.sched_getaffinity(0)))))")

k=0
for idx in 0 1 2; do
    i=${INSTS[$idx]}
    span="${CPUS[$((k*4))]},${CPUS[$((k*4+1))]},${CPUS[$((k*4+2))]},${CPUS[$((k*4+3))]}"
    taskset -c "$span" "$PY" "$ROOT/tools/run_instance.py" "$i" "${TLS[$idx]}" \
        "$OUT/prob_$i.json" > "$OUT/prob_$i.log" 2>&1 &
    k=$((k+1))
done
wait
echo done > "$OUT/DONE"
