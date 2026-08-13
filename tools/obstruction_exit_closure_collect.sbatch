#!/usr/bin/env bash
#SBATCH -p titan
#SBATCH -c 1
#SBATCH -t 00:05:00
#SBATCH -J jasg-closure-collect
#SBATCH -o runs/obstruction-exit-closure-collect-%j.log
set -euo pipefail

ROOT="${OGC_ROOT:-$PWD}"
OUT="${1:?result directory required}"
PY="${OGC_PY312:-$ROOT/.miniforge/envs/ogc2026/bin/python}"
if [[ ! -x "$PY" ]]; then
  COMMON_DIR="$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir)"
  PY="$(dirname "$COMMON_DIR")/.miniforge/envs/ogc2026/bin/python"
fi
"$PY" "$ROOT/tools/obstruction_exit_closure.py" collect "$OUT"
