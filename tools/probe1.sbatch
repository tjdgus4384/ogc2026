#!/usr/bin/env bash
#SBATCH --job-name=probe
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=00:08:00
# One probe, one board, one node -- kept under 5 minutes of solve so the
# exploration loop stays fast. Args: <tool.py> <prob.json> <out> [tool args...]
set -uo pipefail
ROOT=/data_fast/home/tjdgus4384/priv/ogc2026
PY=$ROOT/.miniforge/envs/ogc2026/bin/python
export OMP_NUM_THREADS=4
cd $ROOT
TOOL="$1"; PROB="$2"; OUT="$3"; shift 3
echo "# build $(git rev-parse --short HEAD) job ${SLURM_JOB_ID:-na} $(date -Iseconds) tool=$TOOL prob=$PROB"
$PY "$ROOT/$TOOL" "$PROB" "$OUT" "$@" || echo "{\"prob\":\"$PROB\",\"error\":true}"
