#!/usr/bin/env bash
#SBATCH --job-name=rapid-root
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --output=/dev/null
set -euo pipefail
# Args: SNAP OUT SECONDS_PER_ARM PY AXES_CSV INSTANCES_CSV

SNAP="$1"
OUT="$2"
SECONDS_PER_ARM="$3"
PY="$4"
AXES_CSV="${5:-assignment,schedule,crossover,coalition}"
INSTANCES_CSV="${6:-4,5}"

export PYTHONPATH="$SNAP"
export OMP_NUM_THREADS=4
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export OGC_DIAG=1

IFS=',' read -r -a axes <<< "$AXES_CSV"
IFS=',' read -r -a instances <<< "$INSTANCES_CSV"
instance_count="${#instances[@]}"
axis_index=$((SLURM_ARRAY_TASK_ID / instance_count))
instance_index=$((SLURM_ARRAY_TASK_ID % instance_count))
axis="${axes[$axis_index]}"
instance="${instances[$instance_index]}"
problem="$SNAP/data/prob_${instance}.json"
solution="$OUT/${axis}-p${instance}.sol.json"
receipt="$OUT/${axis}-p${instance}.json"

case "$axis" in
  assignment)
    args=("$SNAP/tools/rapid_assignment.py" "$problem" "$SNAP/seeds/best/prob_${instance}.sol.json" "$solution" "$receipt")
    ;;
  schedule)
    args=("$SNAP/tools/rapid_schedule.py" "$problem" "$SNAP/seeds/best/prob_${instance}.sol.json" "$solution" "$receipt")
    ;;
  crossover)
    args=("$SNAP/tools/rapid_crossover.py" "$problem" "$SNAP/seeds/population-p${instance}.txt" "$solution" "$receipt")
    ;;
  coalition)
    args=("$SNAP/tools/rapid_coalition.py" "$problem" "$SNAP/seeds/best/prob_${instance}.sol.json" "$solution" "$receipt")
    ;;
  *) echo "unknown axis: $axis" >&2; exit 2;;
esac

srun --exclusive --nodes=1 --ntasks=1 --cpus-per-task=4 --mem=16G --cpu-bind=cores \
  timeout --signal=TERM --kill-after=10 "$((SECONDS_PER_ARM + 45))s" \
  "$PY" "${args[@]}" --seconds "$SECONDS_PER_ARM" --seed 7
