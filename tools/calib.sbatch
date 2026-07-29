#!/usr/bin/env bash
#SBATCH --job-name=probe
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=00:45:00
#SBATCH --output=/dev/null
# Server-envelope calibration: v2.1 at LAMBDA x official TLs, two boards per
# node. Args: TAG I1 TL1 I2 TL2 [SEED]
set -euo pipefail
ROOT=/data_fast/home/tjdgus4384/priv/ogc2026
PY=$ROOT/.miniforge/envs/ogc2026/bin/python
TAG="$1"; I1="$2"; TL1="$3"; I2="$4"; TL2="$5"; SEED="${6:-7}"
OUT=$ROOT/runs/$TAG; mkdir -p "$OUT"
export OGC_SOLVER=$ROOT/solver OGC_DATA=preliminary_test OMP_NUM_THREADS=4
export OGC_DIAG=1 OGC_SEED=$SEED OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
mapfile -t CPUS < <($PY -c "import os;print('\n'.join(map(str,sorted(os.sched_getaffinity(0)))))")
taskset -c "${CPUS[0]},${CPUS[1]},${CPUS[2]},${CPUS[3]}" \
  "$PY" $ROOT/tools/run_instance.py "$I1" "$TL1" "$OUT/prob_$I1.json" > "$OUT/prob_$I1.log" 2>&1 &
taskset -c "${CPUS[4]},${CPUS[5]},${CPUS[6]},${CPUS[7]}" \
  "$PY" $ROOT/tools/run_instance.py "$I2" "$TL2" "$OUT/prob_$I2.json" > "$OUT/prob_$I2.log" 2>&1 &
wait
echo done > "$OUT/DONE"
