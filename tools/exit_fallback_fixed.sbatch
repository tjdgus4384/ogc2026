#!/usr/bin/env bash
#SBATCH -J exit-fi
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -t 00:30:00
#SBATCH --output=runs/exit-fallback-fixed-%j.log
set -euo pipefail

REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="${OGC_PY312:-$REPO/.miniforge/envs/ogc2026/bin/python}"
SOLVER="${1:-$REPO/builds/exitlifo1}"
OUT="${2:-$REPO/runs/exit-fallback-fi1}"
ITERS="${3:-20000}"
SEEDS="${4:-7 11 23 41}"
mkdir -p "$OUT"
unset OGC_EXIT_TWIN OGC_EXIT_TWIN_SHADOW OGC_EXIT_TWIN_POST

# One common fixed-oracle construction per instance.  Every stochastic arm
# below begins from these exact bytes.
for inst in 3 4 5 6; do
  env -u OGC_EXIT_TWIN -u OGC_EXIT_TWIN_SHADOW \
    "$PY" "$REPO/tools/exit_fallback_fixed.py" base \
    "$SOLVER" "$inst" "$OUT/p${inst}-base.json" &
done
wait

core=0
for inst in 3 4 5 6; do
  for seed in $SEEDS; do
    (
      for mode in off shadow on; do
        out="$OUT/p${inst}-s${seed}-${mode}.json"
        if [[ "$mode" == shadow ]]; then
          envs=(env -u OGC_EXIT_TWIN OGC_EXIT_TWIN_SHADOW=1)
        elif [[ "$mode" == on ]]; then
          envs=(env -u OGC_EXIT_TWIN_SHADOW OGC_EXIT_TWIN=1)
        else
          envs=(env -u OGC_EXIT_TWIN -u OGC_EXIT_TWIN_SHADOW)
        fi
        OMP_NUM_THREADS=1 taskset -c "$core" "${envs[@]}" \
          "$PY" "$REPO/tools/exit_fallback_fixed.py" run \
          "$SOLVER" "$inst" "$seed" "$ITERS" "$mode" \
          "$OUT/p${inst}-base.json" "$out"
      done
    ) &
    core=$((core + 1))
  done
done
wait

"$PY" "$REPO/tools/exit_fallback_fixed.py" collect "$OUT"
