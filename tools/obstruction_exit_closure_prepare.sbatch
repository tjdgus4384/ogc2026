#!/usr/bin/env bash
#SBATCH -p titan
#SBATCH -c 1
#SBATCH -t 00:15:00
#SBATCH --array=0-15%8
#SBATCH -J jasg-closure-prep
#SBATCH -o runs/obstruction-exit-closure-prepare-%A_%a.log
set -euo pipefail

ROOT="${OGC_ROOT:-$PWD}"
REFERENCE_SOLVER="${1:?reference build required}"
CONSTRUCTIONS="${2:?construction directory required}"
REFERENCE_REPORTS="${3:?job-75041 report directory required}"
OUT="${4:?persistent plateau directory required}"
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

seeds=(7 11 23 41)
instance=$((3 + SLURM_ARRAY_TASK_ID / 4))
seed="${seeds[$((SLURM_ARRAY_TASK_ID % 4))]}"
mkdir -p "$OUT"
"$PY" "$ROOT/tools/obstruction_exit_closure.py" prepare \
  "$REFERENCE_SOLVER" "$CONSTRUCTIONS" "$REFERENCE_REPORTS" \
  "$instance" "$seed" "$OUT/p${instance}-s${seed}.json"
