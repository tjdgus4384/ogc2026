#!/usr/bin/env bash
#SBATCH -p titan
#SBATCH --exclusive
#SBATCH -c 48
#SBATCH -t 00:20:00
#SBATCH -J jasg-stay-rp
#SBATCH -o runs/voluntary-stay-repair-%j.log
set -uo pipefail

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
[[ -x "$PY" ]] || { echo "Python 3.12 environment not found: $PY" >&2; exit 2; }

CANDIDATE="${1:?candidate build required}"
REFERENCE="${2:-$ROOT/builds/jasg-stay-rollout}"
MANIFESTS="${3:-$ROOT/results/voluntary-stay-rollout-74969}"
OUT="${4:-$ROOT/runs/voluntary-stay-repair-${SLURM_JOB_ID}}"
ITERS="${5:-1000}"
unset OGC_CAUSAL_BAY_RNG OGC_KEEP_ASSIGNMENT_LOCK OGC_PHASE2_ORIS
unset OGC_EXIT_TWIN OGC_EXIT_TWIN_SHADOW OGC_PREFP
mkdir -p "$OUT"

pids=()
core=0
for instance in 3 4 5 6; do
  for seed in 7 11 23 41; do
    for mode in reference control stay; do
      solver="$CANDIDATE"
      if [[ "$mode" == reference ]]; then solver="$REFERENCE"; fi
      OMP_NUM_THREADS=1 taskset -c "$core" \
        "$PY" "$ROOT/tools/voluntary_stay_repair.py" run \
        "$solver" "$MANIFESTS" "$instance" "$seed" "$ITERS" "$mode" \
        "$OUT/p${instance}-s${seed}-${mode}.json" &
      pids+=("$!")
      core=$((core + 1))
    done
  done
done

status=0
for pid in "${pids[@]}"; do
  if ! wait "$pid"; then status=1; fi
done
if (( status != 0 )); then exit "$status"; fi
"$PY" "$ROOT/tools/voluntary_stay_repair.py" collect "$OUT"
