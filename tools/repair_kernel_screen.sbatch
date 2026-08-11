#!/usr/bin/env bash
#SBATCH --job-name=repair-kernel-screen
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --output=/dev/null
set -euo pipefail
SNAP="$1"; OUT="$2"; SECONDS_PER_KERNEL="$3"; PY="$4"
export PYTHONPATH="$SNAP:$SNAP/tools" OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 OGC_DIAG=1
kernels=(repair_scored repair_exact repair_nfp repair_lookahead)
instances=(4 5)
kernel="${kernels[$((SLURM_ARRAY_TASK_ID / 2))]}"; instance="${instances[$((SLURM_ARRAY_TASK_ID % 2))]}"
problem="$SNAP/data/prob_${instance}.json"; seed_board="$SNAP/baseline/prob_${instance}.sol.json"
solution="$OUT/${kernel}-p${instance}.sol.json"; receipt="$OUT/${kernel}-p${instance}.json"; verified="$OUT/${kernel}-p${instance}.verified.json"
srun --exclusive --nodes=1 --ntasks=1 --cpus-per-task=4 --mem=16G --cpu-bind=cores \
 timeout --signal=TERM --kill-after=10 "$((SECONDS_PER_KERNEL + 45))s" \
 "$PY" "$SNAP/tools/cpp_kernel_screen.py" "$kernel" "$problem" "$seed_board" "$solution" "$receipt" --seconds "$SECONDS_PER_KERNEL" --seed 7
"$PY" - "$problem" "$solution" "$receipt" "$verified" <<'PY'
import hashlib,json,pathlib,sys
from solver.utils import check_feasibility
problem_path,solution_path,receipt_path,verified_path=map(pathlib.Path,sys.argv[1:])
r=check_feasibility(json.loads(problem_path.read_text()),json.loads(solution_path.read_text()))
if r.get('feasible') is not True: raise SystemExit('repair kernel board failed checker replay')
verified_path.write_text(json.dumps({'schemaVersion':1,'feasible':True,'objective':r['objective'],'obj1':r['obj1'],'obj2':r['obj2'],'obj3':r['obj3'],'solutionSha256':hashlib.sha256(solution_path.read_bytes()).hexdigest(),'receiptSha256':hashlib.sha256(receipt_path.read_bytes()).hexdigest()},sort_keys=True,separators=(',',':'))+'\n')
PY
