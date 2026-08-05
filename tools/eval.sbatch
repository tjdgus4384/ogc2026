#!/usr/bin/env bash
#SBATCH --job-name=eval
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

TAG="$1"; TL="$2"; SOLVER="$3"; INSTS="$4"; PSEED="${5:-7}"
RUNNER="${6:-$ROOT/tools/run_instance.py}"

OUT=$ROOT/runs/$TAG
mkdir -p "$OUT"

export OGC_SOLVER="$SOLVER"
export OGC_ROOT="$ROOT"
export OGC_DATA="${OGC_DATA:-train}"
export OMP_NUM_THREADS=4
export OGC_DIAG=1
export OGC_SEED=$PSEED
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1

# Pin each slot to 4 of the cores Slurm actually gave us (absolute ids differ
# per node, so read the cgroup mask instead of assuming 0..39).
mapfile -t CPUS < <($PY -c "import os;print('\n'.join(map(str,sorted(os.sched_getaffinity(0)))))")

k=0
for i in $INSTS; do
    if [[ "$TL" == "official" ]]; then
        case "$i" in
            1) ITL=60;; 2) ITL=120;; 3) ITL=240;;
            4) ITL=480;; 5) ITL=600;; 6) ITL=900;;
            *) echo "no official time limit for instance $i" >&2; exit 2;;
        esac
    else
        ITL="$TL"
    fi
    span="${CPUS[$((k*4))]},${CPUS[$((k*4+1))]},${CPUS[$((k*4+2))]},${CPUS[$((k*4+3))]}"
    (
        ulimit -v $((16 * 1024 * 1024))
        timeout --signal=TERM --kill-after=10 "$(( ${ITL%.*} + 30 ))s" \
            taskset -c "$span" "$PY" "$RUNNER" "$i" "$ITL" \
            "$OUT/prob_$i.json"
    ) > "$OUT/prob_$i.log" 2>&1 &
    k=$((k+1))
done
wait
