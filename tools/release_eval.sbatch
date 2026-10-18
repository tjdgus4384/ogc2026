#!/usr/bin/env bash
#SBATCH -J relcase
#SBATCH -p titan
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH -c 4
#SBATCH --mem=16G
#
# One shipcheck case in the grading envelope (4 CPU / 16 GB). Submitted by
# tools/release_submit.py with all paths and lineage hashes explicit; the
# case receipt is written by tools/release_run_instance.py.
#
# Usage (via release_submit.py):
#   sbatch tools/release_eval.sbatch RUNNER_ARGS_JSON
set -euo pipefail

PY="${OGC_PY312:-/data_fast/home/tjdgus4384/priv/ogc2026/.miniforge/envs/ogc2026/bin/python}"
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
ARGS_JSON="$1"

export OMP_NUM_THREADS=4
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1

# args file: JSON array of exact release_run_instance.py argv entries
mapfile -t ARGS < <("$PY" -c '
import json, sys
for a in json.load(open(sys.argv[1])):
    print(a)
' "$ARGS_JSON")

exec "$PY" "$REPO/tools/release_run_instance.py" "${ARGS[@]}" --python "$PY"
