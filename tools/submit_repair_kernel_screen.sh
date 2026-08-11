#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; TAG="${1:?usage: submit_repair_kernel_screen.sh <tag> [seconds]}"; SECONDS_PER_KERNEL="${2:-120}"
PY="$ROOT/.miniforge/envs/ogc2026/bin/python"; RUN="$ROOT/runs/$TAG"; SNAP="$RUN/snapshot"; LOCK="$ROOT/runs/.repair-kernel-screen.lock"
[[ "$TAG" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ && "$SECONDS_PER_KERNEL" =~ ^[1-9][0-9]*$ ]] || { echo invalid-arguments >&2; exit 2; }
[[ ! -e "$RUN" ]] || { echo "refusing to overwrite $RUN" >&2; exit 2; }
exec 9>"$LOCK"; flock -n 9 || { echo repair-submit-busy >&2; exit 2; }
queue="$(squeue -r -h -u "$USER" -o '%A_%a')"; queued=0; [[ -z "$queue" ]] || { mapfile -t rows <<< "$queue"; queued="${#rows[@]}"; }; (( queued + 8 <= 8 )) || { echo "refusing: $queued + 8 exceeds 8" >&2; exit 2; }
mkdir -p "$SNAP/tools" "$SNAP/solver/src" "$SNAP/data" "$SNAP/baseline" "$RUN"
"$PY" - "$ROOT" "$SNAP" <<'PY'
import hashlib,json,pathlib,shutil,sys
root,snap=map(pathlib.Path,sys.argv[1:])
for name in ('cpp_kernel_screen.py','rapid_schedule.py','repair_kernel_screen.sbatch','submit_repair_kernel_screen.sh'): shutil.copy2(root/'tools'/name,snap/'tools'/name)
for name in ('myalgorithm.py','utils.py'): shutil.copy2(root/'solver'/name,snap/'solver'/name)
for name in ('api.cpp','repair_scored.hpp','repair_exact_coalition.hpp','repair_nfp_contact.hpp','repair_lookahead.hpp'): shutil.copy2(root/'solver'/'src'/name,snap/'solver'/'src'/name)
exts=sorted((root/'solver').glob('sscore*.so')); assert len(exts)==1; shutil.copy2(exts[0],snap/'solver'/exts[0].name)
for n in (4,5):
 shutil.copy2(root/'data'/'preliminary_test'/f'prob_{n}.json',snap/'data'/f'prob_{n}.json')
 shutil.copy2(root/'archive'/'best-boards-2026-08-04'/f'prob_{n}.sol.json',snap/'baseline'/f'prob_{n}.sol.json')
files={str(p.relative_to(snap)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(x for x in snap.rglob('*') if x.is_file())}; (snap/'MANIFEST.json').write_text(json.dumps({'schemaVersion':1,'files':files},sort_keys=True,indent=2)+'\n')
PY
chmod -R a-w "$SNAP"
job_id="$(sbatch --parsable --partition=titan --array='0-7%8' --output="$RUN/%A_%a.log" --error="$RUN/%A_%a.log" "$SNAP/tools/repair_kernel_screen.sbatch" "$SNAP" "$RUN" "$SECONDS_PER_KERNEL" "$PY")"
"$PY" - "$RUN/META.json" "$TAG" "$job_id" "$SECONDS_PER_KERNEL" <<'PY'
import json,pathlib,sys
p,t,j,s=sys.argv[1:]; pathlib.Path(p).write_text(json.dumps({'schemaVersion':1,'tag':t,'jobId':j,'seconds':int(s),'arrayTasks':8,'partition':'titan','kernels':['repair_scored','repair_exact','repair_nfp','repair_lookahead'],'instances':[4,5]},sort_keys=True,indent=2)+'\n')
PY
printf 'submitted tag=%s job_id=%s tasks=8\n' "$TAG" "$job_id"
