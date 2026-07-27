#!/usr/bin/env bash
#SBATCH --job-name=ogc-eval
#SBATCH --partition=dept
#SBATCH --array=1-40
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --output=/dev/null
#
# One array task = one training instance, on 4 dedicated cores with 16 GB --
# the grading server's exact resource envelope (problem_statement.md §3.2:
# "At most 4 CPU cores (400% CPU usage)", "At most 16 GB of memory").
#
# Submit through tools/submit_eval.sh, which sets TAG / TL / --time and creates
# the output directory. Do not sbatch this file directly.
set -euo pipefail

ROOT=/data_fast/home/tjdgus4384/priv/ogc2026
PY=$ROOT/.miniforge/envs/ogc2026/bin/python

: "${TAG:?TAG not set -- submit via tools/submit_eval.sh}"
: "${TL:?TL not set -- submit via tools/submit_eval.sh}"

OUT=$ROOT/runs/$TAG
mkdir -p "$OUT"

# Pin thread count to the allocation. os.cpu_count() reports the whole node
# (40), not the cgroup, so anything reading it would oversubscribe 10x.
export OMP_NUM_THREADS=4
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1

i=$SLURM_ARRAY_TASK_ID
"$PY" "$ROOT/tools/run_instance.py" "$i" "$TL" "$OUT/prob_$i.json" \
    > "$OUT/prob_$i.log" 2>&1
