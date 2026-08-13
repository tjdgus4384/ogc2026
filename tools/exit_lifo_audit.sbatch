#!/usr/bin/env bash
#SBATCH -J exit-lifo
#SBATCH -N 1
#SBATCH -c 4
#SBATCH --mem=8G
#SBATCH -t 00:15:00
#SBATCH --output=runs/exit-lifo-%j.log
set -euo pipefail

REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="${OGC_PY312:-$REPO/.miniforge/envs/ogc2026/bin/python}"
SOLVER="${1:-$REPO/builds/flexprobe}"
SOURCE_REPO="${2:-/data_fast/home/tjdgus4384/priv/ogc2026}"
OUT="${3:-$REPO/runs/exit-lifo-${SLURM_JOB_ID}.json}"

mkdir -p "$REPO/runs"
"$PY" "$REPO/tools/exit_lifo_audit.py" "$SOLVER" \
    --source-repo "$SOURCE_REPO" --output "$OUT"
