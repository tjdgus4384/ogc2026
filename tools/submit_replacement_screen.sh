#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAG="${1:?usage: submit_replacement_screen.sh <unique-tag> [seconds-per-pipeline]}"
SECONDS_PER_PIPELINE="${2:-90}"
PY="$ROOT/.miniforge/envs/ogc2026/bin/python"
RUN="$ROOT/runs/$TAG"
SNAP="$RUN/snapshot"
LOCK="$ROOT/runs/.replacement-screen-submit.lock"

[[ "$TAG" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo "invalid tag" >&2; exit 2; }
[[ "$SECONDS_PER_PIPELINE" =~ ^[1-9][0-9]*$ ]] || { echo "seconds must be positive integer" >&2; exit 2; }
[[ -x "$PY" ]] || { echo "missing pinned python" >&2; exit 2; }
[[ ! -e "$RUN" ]] || { echo "refusing to overwrite $RUN" >&2; exit 2; }

exec 9>"$LOCK"
flock -n 9 || { echo "another replacement submission is active" >&2; exit 2; }
queue="$(squeue -r -h -u "$USER" -o '%A_%a')" || { echo "squeue failed" >&2; exit 2; }
queued=0
[[ -z "$queue" ]] || { mapfile -t rows <<< "$queue"; queued="${#rows[@]}"; }
(( queued + 8 <= 8 )) || { echo "refusing: $queued active tasks + 8 exceeds 8" >&2; exit 2; }

mkdir -p "$SNAP/tools" "$SNAP/solver" "$SNAP/data" "$SNAP/baseline" "$RUN"
"$PY" - "$ROOT" "$SNAP" <<'PY'
import hashlib, json, pathlib, shutil, sys
root, snap = map(pathlib.Path, sys.argv[1:])
for name in (
    "pipeline_beam_construct.py", "pipeline_temporal_first.py",
    "pipeline_logic_decomp.py", "pipeline_island_coevolution.py",
    "replacement_screen.sbatch", "submit_replacement_screen.sh",
):
    source = root / "tools" / name
    if not source.is_file():
        raise SystemExit(f"missing replacement pipeline source: {source}")
    shutil.copy2(source, snap / "tools" / name)
for name in ("myalgorithm.py", "utils.py"):
    shutil.copy2(root / "solver" / name, snap / "solver" / name)
extensions = sorted((root / "solver").glob("sscore*.so"))
if len(extensions) != 1:
    raise SystemExit(f"expected one solver extension, found {len(extensions)}")
shutil.copy2(extensions[0], snap / "solver" / extensions[0].name)
for instance in (4, 5):
    shutil.copy2(root / "data" / "preliminary_test" / f"prob_{instance}.json",
                 snap / "data" / f"prob_{instance}.json")
    shutil.copy2(root / "archive" / "best-boards-2026-08-04" / f"prob_{instance}.sol.json",
                 snap / "baseline" / f"prob_{instance}.sol.json")
files = {}
for path in sorted(item for item in snap.rglob("*") if item.is_file()):
    files[str(path.relative_to(snap))] = hashlib.sha256(path.read_bytes()).hexdigest()
(snap / "MANIFEST.json").write_text(
    json.dumps({"schemaVersion": 1, "files": files}, sort_keys=True, indent=2) + "\n"
)
PY
chmod -R a-w "$SNAP"
job_id="$(sbatch --parsable --partition=titan --array='0-7%8' \
  --output="$RUN/%A_%a.log" --error="$RUN/%A_%a.log" \
  "$SNAP/tools/replacement_screen.sbatch" "$SNAP" "$RUN" "$SECONDS_PER_PIPELINE" "$PY")"
"$PY" - "$RUN/META.json" "$TAG" "$job_id" "$SECONDS_PER_PIPELINE" <<'PY'
import json, pathlib, sys
path, tag, job_id, seconds = sys.argv[1:]
pathlib.Path(path).write_text(json.dumps({
    "schemaVersion": 1,
    "tag": tag,
    "jobId": job_id,
    "arrayTasks": 8,
    "secondsPerPipeline": int(seconds),
    "partition": "titan",
    "pipelines": ["beam_construct", "temporal_first", "logic_decomp", "island_coevolution"],
    "instances": [4, 5],
}, sort_keys=True, indent=2) + "\n")
PY
printf 'submitted tag=%s job_id=%s tasks=8\n' "$TAG" "$job_id"
