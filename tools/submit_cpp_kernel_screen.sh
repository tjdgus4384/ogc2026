#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAG="${1:?usage: submit_cpp_kernel_screen.sh <unique-tag> [seconds-per-kernel]}"
SECONDS_PER_KERNEL="${2:-120}"
MODE="${3:-all}"
if [[ "$MODE" == "all" ]]; then
  ARRAY_SPEC="0-7%8"
  TASKS=8
elif [[ "$MODE" == "parallel-only" ]]; then
  ARRAY_SPEC="6-7%2"
  TASKS=2
else
  echo "mode must be all or parallel-only" >&2
  exit 2
fi
PY="$ROOT/.miniforge/envs/ogc2026/bin/python"
RUN="$ROOT/runs/$TAG"
SNAP="$RUN/snapshot"
LOCK="$ROOT/runs/.cpp-kernel-screen-submit.lock"
[[ "$TAG" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo "invalid tag" >&2; exit 2; }
[[ "$SECONDS_PER_KERNEL" =~ ^[1-9][0-9]*$ ]] || { echo "seconds must be positive integer" >&2; exit 2; }
[[ -x "$PY" ]] || { echo "missing pinned python" >&2; exit 2; }
[[ ! -e "$RUN" ]] || { echo "refusing to overwrite $RUN" >&2; exit 2; }
exec 9>"$LOCK"
flock -n 9 || { echo "another C++ kernel submission is active" >&2; exit 2; }
queue="$(squeue -r -h -u "$USER" -o '%A_%a')" || { echo "squeue failed" >&2; exit 2; }
queued=0
[[ -z "$queue" ]] || { mapfile -t rows <<< "$queue"; queued="${#rows[@]}"; }
(( queued + TASKS <= 8 )) || { echo "refusing: $queued active tasks + $TASKS exceeds 8" >&2; exit 2; }
mkdir -p "$SNAP/tools" "$SNAP/solver/src" "$SNAP/data" "$SNAP/baseline" "$RUN"
"$PY" - "$ROOT" "$SNAP" <<'PY'
import hashlib, json, pathlib, shutil, sys
root, snap = map(pathlib.Path, sys.argv[1:])
for name in ("cpp_kernel_screen.py", "cpp_kernel_screen.sbatch", "submit_cpp_kernel_screen.sh", "rapid_schedule.py"):
    shutil.copy2(root / "tools" / name, snap / "tools" / name)
for name in ("myalgorithm.py", "utils.py"):
    shutil.copy2(root / "solver" / name, snap / "solver" / name)
for path in sorted((root / "solver" / "src").glob("*.hpp")) + [root / "solver" / "src" / "api.cpp"]:
    shutil.copy2(path, snap / "solver" / "src" / path.name)
extensions = sorted((root / "solver").glob("sscore*.so"))
if len(extensions) != 1:
    raise SystemExit(f"expected one solver extension, found {len(extensions)}")
shutil.copy2(extensions[0], snap / "solver" / extensions[0].name)
for instance in (4, 5):
    shutil.copy2(root / "data" / "preliminary_test" / f"prob_{instance}.json", snap / "data" / f"prob_{instance}.json")
    shutil.copy2(root / "archive" / "best-boards-2026-08-04" / f"prob_{instance}.sol.json", snap / "baseline" / f"prob_{instance}.sol.json")
files = {str(path.relative_to(snap)): hashlib.sha256(path.read_bytes()).hexdigest()
         for path in sorted(item for item in snap.rglob("*") if item.is_file())}
(snap / "MANIFEST.json").write_text(json.dumps({"schemaVersion": 1, "files": files}, sort_keys=True, indent=2) + "\n")
PY
chmod -R a-w "$SNAP"
job_id="$(sbatch --parsable --partition=titan --array="$ARRAY_SPEC" \
  --output="$RUN/%A_%a.log" --error="$RUN/%A_%a.log" \
  "$SNAP/tools/cpp_kernel_screen.sbatch" "$SNAP" "$RUN" "$SECONDS_PER_KERNEL" "$PY")"
"$PY" - "$RUN/META.json" "$TAG" "$job_id" "$SECONDS_PER_KERNEL" "$TASKS" "$MODE" <<'PY'
import json, pathlib, sys
path, tag, job_id, seconds, tasks, mode = sys.argv[1:]
pathlib.Path(path).write_text(json.dumps({"schemaVersion": 1, "tag": tag, "jobId": job_id,
 "arrayTasks": int(tasks), "arrayMode": mode, "secondsPerKernel": int(seconds), "partition": "titan",
 "kernels": ["memetic", "beam_check", "decomp", "parallel_bandit"], "instances": [4, 5]}, sort_keys=True, indent=2) + "\n")
PY
printf 'submitted tag=%s job_id=%s tasks=%s mode=%s\n' "$TAG" "$job_id" "$TASKS" "$MODE"
