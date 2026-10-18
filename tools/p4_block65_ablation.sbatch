#!/usr/bin/env bash
#SBATCH -p titan
#SBATCH -c 1
#SBATCH --mem=2G
#SBATCH -t 00:05:00
#SBATCH -J p4-b65-audit
#SBATCH -o /data_fast/home/tjdgus4384/priv/ogc2026/runs/p4-b65-audit-%j.out
#SBATCH -e /data_fast/home/tjdgus4384/priv/ogc2026/runs/p4-b65-audit-%j.err
set -euo pipefail

ROOT=/data_fast/home/tjdgus4384/priv/ogc2026-worktrees/codex-fundamental
PY=/data_fast/home/tjdgus4384/priv/ogc2026/.miniforge/envs/ogc2026/bin/python
REPORT="$ROOT/runs/flexible-repack-486d29b/p4-s7.json"
OUTPUT="$ROOT/runs/flexible-repack-486d29b/p4-s7-block65-only.json"
RUNTIME="$ROOT/runs/p4-b65-audit-runtime-${SLURM_JOB_ID:?}"

[[ -x "$PY" ]] || { echo "missing Python: $PY" >&2; exit 2; }
[[ -f "$REPORT" ]] || { echo "missing report: $REPORT" >&2; exit 2; }
[[ ! -e "$OUTPUT" && ! -L "$OUTPUT" ]] || {
  echo "refusing to overwrite: $OUTPUT" >&2
  exit 2
}
mkdir -m 0700 "$RUNTIME"
export TMPDIR="$RUNTIME"
export PYTHONNOUSERSITE=1
export PYTHONDONTWRITEBYTECODE=1
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1

cd "$ROOT"
"$PY" tools/p4_block65_ablation.py "$REPORT" "$OUTPUT"
