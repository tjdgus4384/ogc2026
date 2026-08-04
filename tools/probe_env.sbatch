#!/usr/bin/env bash
#SBATCH --job-name=probe
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=00:20:00
# probe1 with an env prefix: ENVSTR is applied before the tool runs.
set -uo pipefail
ROOT=/data_fast/home/tjdgus4384/priv/ogc2026
PY=$ROOT/.miniforge/envs/ogc2026/bin/python
export OMP_NUM_THREADS=4
cd $ROOT
ENVSTR="$1"; TOOL="$2"; PROB="$3"; OUT="$4"; shift 4
echo "# build $(git rev-parse --short HEAD) job ${SLURM_JOB_ID:-na} $(date -Iseconds) env=$ENVSTR"
env $ENVSTR $PY "$ROOT/$TOOL" "$PROB" "$OUT" "$@" || echo "{\"prob\":\"$PROB\",\"error\":true}"
