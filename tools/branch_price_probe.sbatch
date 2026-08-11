#!/usr/bin/env bash
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=0
#SBATCH --exclusive
#SBATCH --time=00:30:00
#SBATCH --output=%x/prob_%a.log
#SBATCH --error=%x/prob_%a.log
set -euo pipefail

SNAPSHOT_ROOT="${1:?snapshot root required}"
PYTHON_BIN="${2:?absolute python path required}"
INSTANCE="${3:?instance required}"
TIME_BUDGET="${4:?time budget required}"
BEAM_WIDTH="${5:?beam width required}"
MAX_STATES="${6:?max states required}"
MAX_CANDIDATES="${7:?max candidates required}"
MAX_ROOT_ROUNDS="${8:?max root rounds required}"
MIN_GAIN_FRACTION="${9:?minimum gain fraction required}"
shift 9

export PYTHONPATH="$SNAPSHOT_ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON_BIN" "$SNAPSHOT_ROOT/tools/run_branch_price_probe.py" \
  "$SNAPSHOT_ROOT/data/prob_${INSTANCE}.json" "$@" \
  --time-budget "$TIME_BUDGET" --beam-width "$BEAM_WIDTH" \
  --max-states "$MAX_STATES" --max-candidates "$MAX_CANDIDATES" \
  --max-root-rounds "$MAX_ROOT_ROUNDS" \
  --min-gain-fraction "$MIN_GAIN_FRACTION" \
  --receipt "$SNAPSHOT_ROOT/../prob_${INSTANCE}.json" \
  --solution "$SNAPSHOT_ROOT/../prob_${INSTANCE}.sol.json"
