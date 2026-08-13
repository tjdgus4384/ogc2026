#!/bin/bash
#SBATCH -J s5split
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -t 00:20:00
#SBATCH -o runs/s5-recovery-%j.log
set -uo pipefail
export PYTHONNOUSERSITE=1
ROOT="${OGC_ROOT:-$PWD}"
if [[ -n "${OGC_PY312:-}" ]]; then
  PY="$OGC_PY312"
elif [[ -x "$ROOT/.miniforge/envs/ogc2026/bin/python" ]]; then
  PY="$ROOT/.miniforge/envs/ogc2026/bin/python"
else
  COMMON_DIR="$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir)"
  CANONICAL_ROOT="$(dirname "$COMMON_DIR")"
  PY="$CANONICAL_ROOT/.miniforge/envs/ogc2026/bin/python"
fi
if [[ ! -x "$PY" ]]; then
  echo "Python 3.12 environment not found: $PY" >&2
  exit 2
fi
OUT="${OGC_S5_OUT:-runs/s5-${SLURM_JOB_ID}}"
mkdir -p "$OUT"
i=0
pids=()
for arg in "prob_5 400 0.15 7" "prob_5 400 0.15 11" "prob_4 400 0.15 7" "prob_5 400 0.30 7"; do
  set -- $arg
  c=$((i*8)); OMP_NUM_THREADS=4 taskset -c $c-$((c+7)) \
    "$PY" -u "$ROOT/tools/s5_split.py" $1 $2 $3 $4 \
    > "$OUT/$1-f$3-s$4.txt" 2>&1 &
  pids+=("$!")
  i=$((i+1))
done
rc=0
for pid in "${pids[@]}"; do
  wait "$pid" || rc=1
done
for f in "$OUT"/*.txt; do echo "##### $(basename $f)"; cat "$f"; done
exit "$rc"
