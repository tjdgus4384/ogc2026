#!/usr/bin/env bash
#SBATCH --job-name=ev36
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=00:25:00
#SBATCH --output=/dev/null
# Judgment eval: P3-P6 ONLY (P1/P2 are variance-zero floor boards --
# user directive 08-01). Args: TAG SOLVER [PSEED]
set -euo pipefail
ROOT=/data_fast/home/tjdgus4384/priv/ogc2026
PY=$ROOT/.miniforge/envs/ogc2026/bin/python
TAG="$1"; SOLVER="$2"; PSEED="${3:-7}"
OUT=$ROOT/runs/$TAG
mkdir -p "$OUT"
export OGC_SOLVER="$SOLVER" OGC_DATA="${OGC_DATA:-preliminary_test}" OMP_NUM_THREADS=4
export OGC_DIAG=1 OGC_SEED=$PSEED OGC_SAVE_SOL=1
[ -n "${OGC_POP:-}" ] && export OGC_POP
[ -n "${OGC_FASTSCAN:-}" ] && export OGC_FASTSCAN
[ -n "${OGC_FLEET:-}" ] && export OGC_FLEET
[ -n "${OGC_BAYSPLIT:-}" ] && export OGC_BAYSPLIT
[ -n "${OGC_BSJOINT:-}" ] && export OGC_BSJOINT
[ -n "${OGC_GLS:-}" ] && export OGC_GLS
[ -n "${OGC_BESTFIT:-}" ] && export OGC_BESTFIT
[ -n "${OGC_ORDSEARCH:-}" ] && export OGC_ORDSEARCH
[ -n "${OGC_SLACKDEF:-}" ] && export OGC_SLACKDEF
[ -n "${OGC_LOCKK:-}" ] && export OGC_LOCKK
[ -n "${OGC_SLIDE:-}" ] && export OGC_SLIDE
[ -n "${OGC_NFP:-}" ] && export OGC_NFP
[ -n "${OGC_LAHC:-}" ] && export OGC_LAHC
[ -n "${OGC_BOXLNS:-}" ] && export OGC_BOXLNS
[ -n "${OGC_REPAIRR:-}" ] && export OGC_REPAIRR
[ -n "${OGC_NFP2:-}" ] && export OGC_NFP2
[ -n "${OGC_SLACKW:-}" ] && export OGC_SLACKW
[ -n "${OGC_LAHCMUL:-}" ] && export OGC_LAHCMUL
[ -n "${OGC_RORDER:-}" ] && export OGC_RORDER
[ -n "${OGC_MIXACC:-}" ] && export OGC_MIXACC
[ -n "${OGC_ORIPREF:-}" ] && export OGC_ORIPREF
[ -n "${OGC_POLYAREA:-}" ] && export OGC_POLYAREA
[ -n "${OGC_MIXDENS:-}" ] && export OGC_MIXDENS
[ -n "${OGC_EJECTALL:-}" ] && export OGC_EJECTALL
[ -n "${OGC_EJECTMUL:-}" ] && export OGC_EJECTMUL
[ -n "${OGC_BOTTLENECK:-}" ] && export OGC_BOTTLENECK
[ -n "${OGC_RESERVE:-}" ] && export OGC_RESERVE
[ -n "${OGC_QFRAC:-}" ] && export OGC_QFRAC
[ -n "${OGC_RETURN:-}" ] && export OGC_RETURN
[ -n "${OGC_SCALESCHED:-}" ] && export OGC_SCALESCHED
[ -n "${OGC_SWAPP:-}" ] && export OGC_SWAPP
[ -n "${OGC_SWAPSEL:-}" ] && export OGC_SWAPSEL
[ -n "${OGC_SWAP3:-}" ] && export OGC_SWAP3
[ -n "${OGC_TAKEOVER:-}" ] && export OGC_TAKEOVER
[ -n "${OGC_CHAINMAX:-}" ] && export OGC_CHAINMAX
[ -n "${OGC_SWAPREL:-}" ] && export OGC_SWAPREL
[ -n "${OGC_REORP:-}" ] && export OGC_REORP
[ -n "${OGC_SHIFTP:-}" ] && export OGC_SHIFTP
[ -n "${OGC_SHIFTLOC:-}" ] && export OGC_SHIFTLOC
[ -n "${OGC_TABU:-}" ] && export OGC_TABU
[ -n "${OGC_TABUP:-}" ] && export OGC_TABUP
[ -n "${OGC_SQUEAK:-}" ] && export OGC_SQUEAK
[ -n "${OGC_SCOMPACT:-}" ] && export OGC_SCOMPACT
TLS=(240 480 600 900)
PS=(3 4 5 6)
mapfile -t CPUS < <($PY -c "import os;print('\n'.join(map(str,sorted(os.sched_getaffinity(0)))))")
k=0
for idx in 0 1 2 3; do
  span="${CPUS[$((k*4))]},${CPUS[$((k*4+1))]},${CPUS[$((k*4+2))]},${CPUS[$((k*4+3))]}"
  taskset -c "$span" "$PY" "$ROOT/tools/run_instance.py" "${PS[$idx]}" "${TLS[$idx]}" \
    "$OUT/prob_${PS[$idx]}.json" > "$OUT/prob_${PS[$idx]}.log" 2>&1 &
  k=$((k+1))
done
wait
echo done > "$OUT/DONE"
