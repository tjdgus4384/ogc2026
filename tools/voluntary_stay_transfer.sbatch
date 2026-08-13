#!/bin/bash
#SBATCH -p titan
#SBATCH --exclusive
#SBATCH -c 32
#SBATCH -t 00:15:00
#SBATCH -J jasg-stay-fi
#SBATCH -o runs/voluntary-stay-transfer-%j.log
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
CONSTRUCTIONS="${2:?sequential construction directory required}"
REFERENCE="${3:-$ROOT/results/assignment-persistent-74963}"
OUT="${4:-$ROOT/runs/voluntary-stay-transfer-${SLURM_JOB_ID}}"
ITERS="${5:-20000}"
unset OGC_PHASE2_ORIS
unset OGC_EXIT_TWIN OGC_EXIT_TWIN_SHADOW OGC_PREFP
# Match the immutable job-74963 A/A environment.  No assignment vector is
# installed in either transfer arm: KEEP therefore creates no lock.  These
# flags only preserve the reference RNG consumption inside op8/op9's temporary
# forced-bay insertion.
export OGC_CAUSAL_BAY_RNG=1
export OGC_KEEP_ASSIGNMENT_LOCK=1
mkdir -p "$OUT"

pids=()
core=0
for instance in 3 4 5 6; do
  for seed in 7 11 23 41; do
    for mode in baseline stay; do
      OMP_NUM_THREADS=1 taskset -c "$core" \
        "$PY" "$ROOT/tools/voluntary_stay_transfer.py" run \
        "$SOLVER" "$CONSTRUCTIONS" "$instance" "$seed" "$ITERS" "$mode" \
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
if [[ "$status" -ne 0 ]]; then exit "$status"; fi
"$PY" "$ROOT/tools/voluntary_stay_transfer.py" collect \
  "$OUT" "$REFERENCE" "$ITERS"
