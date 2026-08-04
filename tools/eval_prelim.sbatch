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
# Args: TAG SOLVER [PSEED] [ARMS] [QMUL] [ROUNDS]
set -euo pipefail

ROOT=/data_fast/home/tjdgus4384/priv/ogc2026
PY=$ROOT/.miniforge/envs/ogc2026/bin/python

TAG="$1"; SOLVER="$2"; PSEED="${3:-7}"; ARMS="${4:-0}"; QMUL="${5:-1}"; ROUNDS="${6:-0}"

OUT=$ROOT/runs/$TAG
mkdir -p "$OUT"

export OGC_SOLVER="$SOLVER"
export OGC_DATA=preliminary_test
export OMP_NUM_THREADS=4
export OGC_DIAG=1
if [ "$PSEED" != "auto" ]; then export OGC_SEED=$PSEED; fi
export OGC_ARMS=$ARMS
export OGC_QMUL=$QMUL
export OGC_ROUNDS=$ROUNDS
# optional kernel screeners: pass through when set in the caller's env
[ -n "${OGC_HCFRAC:-}" ] && export OGC_HCFRAC
[ -n "${OGC_BIGQ:-}" ] && export OGC_BIGQ
[ -n "${OGC_PREFP:-}" ] && export OGC_PREFP
[ -n "${OGC_POP:-}" ] && export OGC_POP
[ -n "${OGC_MARM:-}" ] && export OGC_MARM
[ -n "${OGC_REFINE:-}" ] && export OGC_REFINE
[ -n "${OGC_2PHASE:-}" ] && export OGC_2PHASE
[ -n "${OGC_AREASLK:-}" ] && export OGC_AREASLK
[ -n "${OGC_STRIDE:-}" ] && export OGC_STRIDE
[ -n "${OGC_CNOISE:-}" ] && export OGC_CNOISE
[ -n "${OGC_DECAY:-}" ] && export OGC_DECAY
[ -n "${OGC_OP8TOP:-}" ] && export OGC_OP8TOP
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
