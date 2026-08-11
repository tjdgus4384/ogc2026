#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAG="${1:?usage: submit_cpp_bandit_focus.sh <tag> <seconds> <instances-csv> <seeds-csv> [kernel] [baseline-dir] [data-dir]}"
SECONDS_PER_RUN="${2:?missing seconds}"; INSTANCES_CSV="${3:?missing instances}"; SEEDS_CSV="${4:?missing seeds}"; KERNEL="${5:-bandit}"
BASELINE_ROOT="${6:-$ROOT/archive/best-boards-2026-08-04}"
DATA_ROOT="${7:-$ROOT/data/preliminary_test}"
PY="$ROOT/.miniforge/envs/ogc2026/bin/python"; RUN="$ROOT/runs/$TAG"; SNAP="$RUN/snapshot"; LOCK="$ROOT/runs/.cpp-bandit-focus.lock"
[[ "$TAG" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo invalid-tag >&2; exit 2; }
[[ "$SECONDS_PER_RUN" =~ ^[1-9][0-9]*$ ]] || { echo invalid-seconds >&2; exit 2; }
[[ "$KERNEL" == "bandit" || "$KERNEL" == "parallel_bandit" || "$KERNEL" == "event_order" || "$KERNEL" == "assignment_cycles" || "$KERNEL" == "island_memetic_v2" || "$KERNEL" == "large_ruin" || "$KERNEL" == "backbone_pair" || "$KERNEL" == "backbone_all" || "$KERNEL" == "repair_learned" || "$KERNEL" == "pattern_master" || "$KERNEL" == "parallel_benders" || "$KERNEL" == "mcts_coalition" || "$KERNEL" == "ejection_chain" || "$KERNEL" == "assignment_flow" || "$KERNEL" == "beam_check" || "$KERNEL" == "decomp" ]] || { echo invalid-kernel >&2; exit 2; }
[[ ! -e "$RUN" ]] || { echo "refusing to overwrite $RUN" >&2; exit 2; }
IFS=',' read -r -a instances <<< "$INSTANCES_CSV"; IFS=',' read -r -a seeds <<< "$SEEDS_CSV"
declare -A seen_i=() seen_s=()
for n in "${instances[@]}"; do [[ "$n" =~ ^[1-9][0-9]*$ ]] && (( n <= 40 )) || { echo "invalid instance $n" >&2; exit 2; }; [[ -z "${seen_i[$n]:-}" ]] || exit 2; seen_i[$n]=1; done
for seed in "${seeds[@]}"; do [[ "$seed" =~ ^[0-9]+$ ]] || { echo "invalid seed $seed" >&2; exit 2; }; [[ -z "${seen_s[$seed]:-}" ]] || exit 2; seen_s[$seed]=1; done
tasks=$(( ${#instances[@]} * ${#seeds[@]} )); (( tasks >= 2 && tasks <= 8 )) || { echo "invalid task count $tasks" >&2; exit 2; }
exec 9>"$LOCK"; flock -n 9 || { echo focus-submit-busy >&2; exit 2; }
queue="$(squeue -r -h -u "$USER" -o '%A_%a')"; queued=0; [[ -z "$queue" ]] || { mapfile -t rows <<< "$queue"; queued="${#rows[@]}"; }
(( queued + tasks <= 8 )) || { echo "refusing: $queued + $tasks exceeds 8" >&2; exit 2; }
mkdir -p "$SNAP/tools" "$SNAP/solver/src" "$SNAP/data" "$SNAP/baseline" "$RUN"
"$PY" - "$ROOT" "$SNAP" "$INSTANCES_CSV" "$BASELINE_ROOT" "$SEEDS_CSV" "$DATA_ROOT" <<'PY'
import hashlib,json,pathlib,shutil,sys
root,snap=map(pathlib.Path,sys.argv[1:3]); instances=list(map(int,sys.argv[3].split(','))); baseline=pathlib.Path(sys.argv[4]).resolve(); seeds=list(map(int,sys.argv[5].split(','))); data=pathlib.Path(sys.argv[6]).resolve()
if not baseline.is_relative_to(root.resolve()) or not baseline.is_dir(): raise SystemExit('baseline directory must be inside repository')
if not data.is_relative_to(root.resolve()) or not data.is_dir(): raise SystemExit('data directory must be inside repository')
for name in ('cpp_kernel_screen.py','rapid_schedule.py','cpp_bandit_focus.sbatch','submit_cpp_bandit_focus.sh'): shutil.copy2(root/'tools'/name,snap/'tools'/name)
for name in ('myalgorithm.py','utils.py'): shutil.copy2(root/'solver'/name,snap/'solver'/name)
for path in sorted((root/'solver'/'src').glob('*.hpp'))+[root/'solver'/'src'/'api.cpp']: shutil.copy2(path,snap/'solver'/'src'/path.name)
exts=sorted((root/'solver').glob('sscore*.so')); assert len(exts)==1; shutil.copy2(exts[0],snap/'solver'/exts[0].name)
for n in instances:
 shutil.copy2(data/f'prob_{n}.json',snap/'data'/f'prob_{n}.json')
 source=baseline/f'prob_{n}.sol.json'
 if not source.is_file(): raise SystemExit(f'missing baseline {source}')
 shutil.copy2(source,snap/'baseline'/f'prob_{n}.sol.json')
 for seed in seeds:
  elite=baseline/f'prob_{n}-s{seed}.sol.json'
  if elite.is_file(): shutil.copy2(elite,snap/'baseline'/elite.name)
files={str(p.relative_to(snap)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(x for x in snap.rglob('*') if x.is_file())}
(snap/'MANIFEST.json').write_text(json.dumps({'schemaVersion':1,'files':files},sort_keys=True,indent=2)+'\n')
PY
chmod -R a-w "$SNAP"; max=$((tasks-1))
job_id="$(sbatch --parsable --partition=titan --array="0-${max}%${tasks}" --output="$RUN/%A_%a.log" --error="$RUN/%A_%a.log" "$SNAP/tools/cpp_bandit_focus.sbatch" "$SNAP" "$RUN" "$SECONDS_PER_RUN" "$PY" "$INSTANCES_CSV" "$SEEDS_CSV" "$KERNEL")"
"$PY" - "$RUN/META.json" "$TAG" "$job_id" "$SECONDS_PER_RUN" "$tasks" "$INSTANCES_CSV" "$SEEDS_CSV" "$KERNEL" "$BASELINE_ROOT" "$DATA_ROOT" <<'PY'
import json,pathlib,sys
p,tag,j,s,t,i,se,k,b,d=sys.argv[1:]; pathlib.Path(p).write_text(json.dumps({'schemaVersion':1,'tag':tag,'jobId':j,'seconds':int(s),'arrayTasks':int(t),'partition':'titan','kernel':k,'baselineSource':b,'dataSource':d,'instances':[int(x) for x in i.split(',')],'seeds':[int(x) for x in se.split(',')]},sort_keys=True,indent=2)+'\n')
PY
printf 'submitted tag=%s job_id=%s tasks=%s\n' "$TAG" "$job_id" "$tasks"
