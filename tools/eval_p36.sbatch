#!/usr/bin/env bash
#SBATCH --job-name=ev36
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=00:25:00
#SBATCH --output=/dev/null
# Judgment eval: P3-P6 at the official prelim TLs (240/480/600/900,
# data/prelim_refs.json hidden_specs), 4 pinned cores each = the grading
# envelope. P1/P2 excluded: variance-zero floor boards (user directive 08-01).
#
#   sbatch -p titan tools/eval_p36.sbatch TAG SOLVERDIR [PSEED]
#   read the verdict with: python3 tools/judge.py p36 TAG
#
# OGC_* experiment knobs propagate from the submitting shell via sbatch's
# default --export=ALL; no per-knob plumbing here.
set -euo pipefail
ROOT=/data_fast/home/tjdgus4384/priv/ogc2026
PY=$ROOT/.miniforge/envs/ogc2026/bin/python
TAG="$1"; SOLVER="$2"; PSEED="${3:-7}"
OUT=$ROOT/runs/$TAG
mkdir -p "$OUT"
export OGC_SOLVER="$SOLVER" OGC_DATA="${OGC_DATA:-preliminary_test}" OMP_NUM_THREADS=4
export OGC_DIAG=1 OGC_SEED=$PSEED OGC_SAVE_SOL=1
printf '{"tag":"%s","data":"%s","pseed":%s,"solver_dir":"%s","tls":[240,480,600,900],"solver_commit":"%s","solver_dirty":%s}\n' \
  "$TAG" "${OGC_DATA:-preliminary_test}" "$PSEED" "$(basename "$SOLVER")" \
  "$(cd "$ROOT" && git rev-parse --short HEAD)" \
  "$(cd "$ROOT" && git diff --quiet HEAD -- solver && echo false || echo true)" > "$OUT/META.json"
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
