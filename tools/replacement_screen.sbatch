#!/usr/bin/env bash
#SBATCH --job-name=replacement-screen
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --output=/dev/null
set -euo pipefail
# Args: SNAP OUT SECONDS PY
SNAP="$1"
OUT="$2"
SECONDS_PER_PIPELINE="$3"
PY="$4"

export PYTHONPATH="$SNAP"
export OMP_NUM_THREADS=4
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export OGC_DIAG=1

pipelines=(beam_construct temporal_first logic_decomp island_coevolution)
instances=(4 5)
pipeline_index=$((SLURM_ARRAY_TASK_ID / 2))
instance_index=$((SLURM_ARRAY_TASK_ID % 2))
pipeline="${pipelines[$pipeline_index]}"
instance="${instances[$instance_index]}"
problem="$SNAP/data/prob_${instance}.json"
solution="$OUT/${pipeline}-p${instance}.sol.json"
receipt="$OUT/${pipeline}-p${instance}.json"
verified="$OUT/${pipeline}-p${instance}.verified.json"
script="$SNAP/tools/pipeline_${pipeline}.py"

srun --exclusive --nodes=1 --ntasks=1 --cpus-per-task=4 --mem=16G --cpu-bind=cores \
  timeout --signal=TERM --kill-after=10 "$((SECONDS_PER_PIPELINE + 45))s" \
  "$PY" "$script" "$problem" "$solution" "$receipt" \
  --seconds "$SECONDS_PER_PIPELINE" --seed 7

"$PY" - "$problem" "$solution" "$receipt" "$verified" <<'PY'
import hashlib, json, pathlib, sys
from solver.utils import check_feasibility
problem_path, solution_path, receipt_path, verified_path = map(pathlib.Path, sys.argv[1:])
problem = json.loads(problem_path.read_text())
solution = json.loads(solution_path.read_text())
result = check_feasibility(problem, solution)
if result.get("feasible") is not True:
    raise SystemExit("persisted replacement board failed wrapper checker replay")
proof = {
    "schemaVersion": 1,
    "feasible": True,
    "objective": result["objective"],
    "obj1": result["obj1"],
    "obj2": result["obj2"],
    "obj3": result["obj3"],
    "solutionSha256": hashlib.sha256(solution_path.read_bytes()).hexdigest(),
    "receiptSha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
}
verified_path.write_text(json.dumps(proof, sort_keys=True, separators=(",", ":")) + "\n")
PY
