#!/usr/bin/env bash
#SBATCH -p titan
#SBATCH -c 1
#SBATCH -t 00:25:00
#SBATCH --array=0-47%8
#SBATCH -J jasg-closure-gate
#SBATCH -o runs/obstruction-exit-closure-gate-%A_%a.log
set -euo pipefail

ROOT="${OGC_ROOT:-$PWD}"
CANDIDATE="${1:?candidate build required}"
REFERENCE="${2:?reference build required}"
PLATEAUS="${3:?plateau directory required}"
OUT="${4:?persistent result directory required}"
ITERS="${5:-20000}"
CHECKPOINT="${6:-1000}"
if [[ "$ITERS" != "20000" || "$CHECKPOINT" != "1000" ]]; then
  echo "Frozen gate requires exactly 20000 iterations and checkpoint 1000" >&2
  exit 2
fi
PY="${OGC_PY312:-$ROOT/.miniforge/envs/ogc2026/bin/python}"
if [[ ! -x "$PY" ]]; then
  COMMON_DIR="$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir)"
  PY="$(dirname "$COMMON_DIR")/.miniforge/envs/ogc2026/bin/python"
fi
[[ -x "$PY" ]] || { echo "Python 3.12 environment not found" >&2; exit 2; }

unset OGC_PHASE2_ORIS OGC_EXIT_TWIN OGC_EXIT_TWIN_SHADOW OGC_PREFP
export OGC_CAUSAL_BAY_RNG=1
export OGC_KEEP_ASSIGNMENT_LOCK=1
export OMP_NUM_THREADS=1

modes=(reference control closure)
seeds=(7 11 23 41)
cell=$((SLURM_ARRAY_TASK_ID / 3))
mode="${modes[$((SLURM_ARRAY_TASK_ID % 3))]}"
instance=$((3 + cell / 4))
seed="${seeds[$((cell % 4))]}"
solver="$CANDIDATE"
if [[ "$mode" == reference ]]; then solver="$REFERENCE"; fi

mkdir -p "$OUT"
"$PY" "$ROOT/tools/obstruction_exit_closure.py" run \
  "$solver" "$PLATEAUS/p${instance}-s${seed}.json" \
  "$instance" "$seed" "$ITERS" "$mode" \
  "$OUT/p${instance}-s${seed}-${mode}.json" \
  --checkpoint "$CHECKPOINT"
