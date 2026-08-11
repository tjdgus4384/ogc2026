#!/usr/bin/env bash
#SBATCH --job-name=cpp-bandit-focus
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --output=/dev/null
set -euo pipefail
# Args: SNAP OUT SECONDS PY INSTANCES_CSV SEEDS_CSV KERNEL
SNAP="$1"; OUT="$2"; SECONDS_PER_RUN="$3"; PY="$4"; INSTANCES_CSV="$5"; SEEDS_CSV="$6"; KERNEL="$7"
export PYTHONPATH="$SNAP:$SNAP/tools"
export OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 OGC_DIAG=1
IFS=',' read -r -a instances <<< "$INSTANCES_CSV"
IFS=',' read -r -a seeds <<< "$SEEDS_CSV"
instance_count="${#instances[@]}"
seed="${seeds[$((SLURM_ARRAY_TASK_ID / instance_count))]}"
instance="${instances[$((SLURM_ARRAY_TASK_ID % instance_count))]}"
baseline="$SNAP/baseline/prob_${instance}-s${seed}.sol.json"
[[ -f "$baseline" ]] || baseline="$SNAP/baseline/prob_${instance}.sol.json"
solution="$OUT/${KERNEL}-s${seed}-p${instance}.sol.json"
receipt="$OUT/${KERNEL}-s${seed}-p${instance}.json"
verified="$OUT/${KERNEL}-s${seed}-p${instance}.verified.json"
srun --exclusive --nodes=1 --ntasks=1 --cpus-per-task=4 --mem=16G --cpu-bind=cores \
  timeout --signal=TERM --kill-after=10 "$((SECONDS_PER_RUN + 45))s" \
  "$PY" "$SNAP/tools/cpp_kernel_screen.py" "$KERNEL" "$SNAP/data/prob_${instance}.json" \
  "$baseline" "$solution" "$receipt" \
  --seconds "$SECONDS_PER_RUN" --seed "$seed"
"$PY" - "$SNAP/data/prob_${instance}.json" "$solution" "$receipt" "$verified" <<'PY'
import hashlib,json,pathlib,sys
from solver.utils import check_feasibility
problem_path,solution_path,receipt_path,verified_path=map(pathlib.Path,sys.argv[1:])
r=check_feasibility(json.loads(problem_path.read_text()),json.loads(solution_path.read_text()))
if r.get('feasible') is not True: raise SystemExit('focused bandit board failed checker replay')
verified_path.write_text(json.dumps({'schemaVersion':1,'feasible':True,'objective':r['objective'],'obj1':r['obj1'],'obj2':r['obj2'],'obj3':r['obj3'],'solutionSha256':hashlib.sha256(solution_path.read_bytes()).hexdigest(),'receiptSha256':hashlib.sha256(receipt_path.read_bytes()).hexdigest()},sort_keys=True,separators=(',',':'))+'\n')
PY
