#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAG="${1:?usage: submit_rapid_screen.sh <unique-tag> [seconds-per-arm] [axes-csv] [instances-csv]}"
SECONDS_PER_ARM="${2:-120}"
AXES_CSV="${3:-assignment,schedule,crossover,coalition}"
INSTANCES_CSV="${4:-4,5}"
PY="$ROOT/.miniforge/envs/ogc2026/bin/python"
RUN="$ROOT/runs/$TAG"
SNAP="$RUN/snapshot"
LOCK="$ROOT/runs/.rapid-screen-submit.lock"

[[ "$TAG" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo "invalid tag" >&2; exit 2; }
[[ "$SECONDS_PER_ARM" =~ ^[1-9][0-9]*$ ]] || { echo "seconds must be positive integer" >&2; exit 2; }
[[ -x "$PY" ]] || { echo "missing pinned python" >&2; exit 2; }
[[ ! -e "$RUN" ]] || { echo "refusing to overwrite $RUN" >&2; exit 2; }
IFS=',' read -r -a AXES <<< "$AXES_CSV"
declare -A SEEN_AXES=()
for axis in "${AXES[@]}"; do
  [[ "$axis" == assignment || "$axis" == schedule || "$axis" == crossover || "$axis" == coalition ]] || {
    echo "invalid axis: $axis" >&2; exit 2;
  }
  [[ -z "${SEEN_AXES[$axis]:-}" ]] || { echo "duplicate axis: $axis" >&2; exit 2; }
  SEEN_AXES[$axis]=1
done
IFS=',' read -r -a INSTANCES <<< "$INSTANCES_CSV"
declare -A SEEN_INSTANCES=()
for instance in "${INSTANCES[@]}"; do
  [[ "$instance" =~ ^[3-6]$ ]] || { echo "invalid instance: $instance" >&2; exit 2; }
  [[ -z "${SEEN_INSTANCES[$instance]:-}" ]] || { echo "duplicate instance: $instance" >&2; exit 2; }
  SEEN_INSTANCES[$instance]=1
done
TASKS=$(( ${#AXES[@]} * ${#INSTANCES[@]} ))
(( TASKS >= 2 && TASKS <= 8 )) || { echo "invalid task count: $TASKS" >&2; exit 2; }

exec 9>"$LOCK"
flock -n 9 || { echo "another rapid screen submission is active" >&2; exit 2; }
queue="$(squeue -r -h -u "$USER" -o '%A_%a')" || { echo "squeue failed" >&2; exit 2; }
queued=0
[[ -z "$queue" ]] || { mapfile -t rows <<< "$queue"; queued="${#rows[@]}"; }
(( queued + TASKS <= 8 )) || { echo "refusing: $queued active tasks + $TASKS exceeds 8" >&2; exit 2; }

mkdir -p "$SNAP/tools" "$SNAP/solver" "$SNAP/data" "$SNAP/seeds/best" "$SNAP/seeds/population" "$RUN"
"$PY" - "$ROOT" "$SNAP" "$INSTANCES_CSV" <<'PY'
import hashlib, json, pathlib, shutil, sys
root, snap = map(pathlib.Path, sys.argv[1:3])
instances = tuple(int(value) for value in sys.argv[3].split(","))
for name in (
    "rapid_assignment.py", "rapid_schedule.py", "rapid_crossover.py",
    "rapid_coalition.py", "rapid_screen.sbatch", "submit_rapid_screen.sh",
):
    source = root / "tools" / name
    if not source.is_file():
        raise SystemExit(f"missing rapid-screen source: {source}")
    shutil.copy2(source, snap / "tools" / name)
for name in ("myalgorithm.py", "utils.py"):
    shutil.copy2(root / "solver" / name, snap / "solver" / name)
extensions = sorted((root / "solver").glob("sscore*.so"))
if len(extensions) != 1:
    raise SystemExit(f"expected one solver extension, found {len(extensions)}")
shutil.copy2(extensions[0], snap / "solver" / extensions[0].name)
for instance in instances:
    shutil.copy2(root / "data" / "preliminary_test" / f"prob_{instance}.json",
                 snap / "data" / f"prob_{instance}.json")
    best = root / "archive" / "best-boards-2026-08-04" / f"prob_{instance}.sol.json"
    shutil.copy2(best, snap / "seeds" / "best" / best.name)
    population = [best]
    for tag in (
        "matched-v53-b30d1f6", "matched-current-42ae5b5",
        "matched-exactevents-42ae5b5", "matched-eventtime-42ae5b5",
    ):
        population.append(root / "runs" / tag / f"prob_{instance}.sol.json")
    lines = []
    for index, source in enumerate(population):
        target = snap / "seeds" / "population" / f"p{instance}-{index}.sol.json"
        shutil.copy2(source, target)
        lines.append(str(target))
    (snap / "seeds" / f"population-p{instance}.txt").write_text(
        "\n".join(lines) + "\n"
    )
files = {}
for path in sorted(item for item in snap.rglob("*") if item.is_file()):
    files[str(path.relative_to(snap))] = hashlib.sha256(path.read_bytes()).hexdigest()
(snap / "MANIFEST.json").write_text(
    json.dumps({"schemaVersion": 1, "files": files}, sort_keys=True, indent=2) + "\n"
)
PY
chmod -R a-w "$SNAP"

array_max=$((TASKS - 1))
job_id="$(sbatch --parsable --partition=titan --array="0-${array_max}%${TASKS}" \
  --output="$RUN/%A_%a.log" --error="$RUN/%A_%a.log" \
  "$SNAP/tools/rapid_screen.sbatch" "$SNAP" "$RUN" "$SECONDS_PER_ARM" "$PY" "$AXES_CSV" "$INSTANCES_CSV")"
"$PY" - "$RUN/META.json" "$TAG" "$job_id" "$SECONDS_PER_ARM" "$TASKS" "$AXES_CSV" "$INSTANCES_CSV" <<'PY'
import json, pathlib, sys
path, tag, job_id, seconds, tasks, axes, instances = sys.argv[1:]
pathlib.Path(path).write_text(json.dumps({
    "schemaVersion": 1,
    "tag": tag,
    "jobId": job_id,
    "arrayTasks": int(tasks),
    "secondsPerArm": int(seconds),
    "partition": "titan",
    "axes": axes.split(","),
    "instances": [int(value) for value in instances.split(",")],
}, sort_keys=True, indent=2) + "\n")
PY
printf 'submitted tag=%s job_id=%s tasks=%s axes=%s instances=%s\n' "$TAG" "$job_id" "$TASKS" "$AXES_CSV" "$INSTANCES_CSV"
