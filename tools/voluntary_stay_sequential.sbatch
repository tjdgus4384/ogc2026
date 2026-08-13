#!/bin/bash
#SBATCH -p titan
#SBATCH --exclusive
#SBATCH -c 32
#SBATCH -t 00:20:00
#SBATCH -J jasg-stay-seq
#SBATCH -o runs/voluntary-stay-sequential-%j.log
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

SOLVER="${1:-$ROOT/builds/jasg-stay-rollout}"
MANIFEST="${2:-$ROOT/results/voluntary-stay-rollout-74969}"
OUT="${3:-$ROOT/runs/voluntary-stay-sequential-${SLURM_JOB_ID}}"
DEPTH="${4:-2}"
BEAM="${5:-2}"
TOP="${6:-20}"
unset OGC_CAUSAL_BAY_RNG OGC_KEEP_ASSIGNMENT_LOCK OGC_PHASE2_ORIS
unset OGC_EXIT_TWIN OGC_EXIT_TWIN_SHADOW OGC_PREFP
mkdir -p "$OUT"

pids=()
slot=0
for instance in 3 4 5 6; do
  cpu=$((slot * 8))
  OMP_NUM_THREADS=1 taskset -c "$cpu-$((cpu + 7))" \
    "$PY" "$ROOT/tools/voluntary_stay_sequential.py" run \
    "$SOLVER" "$MANIFEST" "$instance" "$OUT/prob_${instance}.json" \
    --depth "$DEPTH" --beam "$BEAM" --top "$TOP" &
  pids+=("$!")
  slot=$((slot + 1))
done

status=0
for pid in "${pids[@]}"; do
  if ! wait "$pid"; then status=1; fi
done
if [[ "$status" -ne 0 ]]; then exit "$status"; fi
"$PY" "$ROOT/tools/voluntary_stay_sequential.py" collect "$OUT"
