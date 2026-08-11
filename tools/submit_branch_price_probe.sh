#!/usr/bin/env bash
# Freeze and submit independent whole-node P4/P5 branch-price probes.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec 9>"$ROOT/runs/.branch-price-submit.lock"
flock -n 9 || { echo "another branch-price submission is in progress" >&2; exit 2; }
TAG="${1:?usage: submit_branch_price_probe.sh <unique-tag> [4,5]}"
INSTANCES="${2:-4,5}"
PYTHON_BIN="${PYTHON_BIN:-$ROOT/.miniforge/envs/ogc2026/bin/python}"
DATA_DIR="${OGC_DATA:-$ROOT/data/preliminary_test}"
SEED_TAGS="${SEED_TAGS:-matched-v53,current,exactevents,eventtime}"
TIME_BUDGET="${TIME_BUDGET:-60}"
BEAM_WIDTH="${BEAM_WIDTH:-64}"
MAX_STATES="${MAX_STATES:-10000}"
MAX_CANDIDATES="${MAX_CANDIDATES:-10000}"
MAX_ROOT_ROUNDS="${MAX_ROOT_ROUNDS:-3}"
MIN_GAIN_P4="${MIN_GAIN_P4:-0.03}"
MIN_GAIN_P5="${MIN_GAIN_P5:-0.02}"
PARTITION="${PARTITION:-dept}"

[[ "$TAG" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo "invalid tag" >&2; exit 2; }
[[ -x "$PYTHON_BIN" ]] || { echo "missing required python: $PYTHON_BIN" >&2; exit 2; }
[[ -d "$DATA_DIR" ]] || { echo "missing data directory: $DATA_DIR" >&2; exit 2; }
[[ ! -e "$ROOT/runs/$TAG" && ! -e "$ROOT/results/$TAG" && ! -e "$ROOT/results/$TAG.json" ]] || {
  echo "refusing to overwrite existing run or results for $TAG" >&2; exit 2;
}
IFS=',' read -r -a INSTS <<< "$INSTANCES"
(( ${#INSTS[@]} >= 1 && ${#INSTS[@]} <= 2 )) || { echo "instance list must contain at most P4,P5" >&2; exit 2; }
declare -A SEEN_INSTANCES=()
for instance in "${INSTS[@]}"; do
  [[ "$instance" == 4 || "$instance" == 5 ]] || { echo "only instances 4 and 5 are allowed" >&2; exit 2; }
  [[ -z "${SEEN_INSTANCES[$instance]:-}" ]] || { echo "duplicate instance: $instance" >&2; exit 2; }
  SEEN_INSTANCES[$instance]=1
  [[ -f "$DATA_DIR/prob_${instance}.json" ]] || { echo "missing problem $instance" >&2; exit 2; }
done
queue_count() {
  local output
  if ! output="$(squeue -r -h -u "$USER" -o '%A_%a')"; then
    echo "failed to query expanded Slurm queue; refusing submission" >&2
    return 2
  fi
  if [[ -z "$output" ]]; then
    printf '0\n'
  else
    local entries=()
    mapfile -t entries <<< "$output"
    printf '%d\n' "${#entries[@]}"
  fi
}
QUEUED_COUNT="$(queue_count)"
if (( QUEUED_COUNT + ${#INSTS[@]} > 8 )); then
  echo "refusing submission: $QUEUED_COUNT queued/running tasks plus ${#INSTS[@]} exceeds 8" >&2
  exit 2
fi

RUN="$ROOT/runs/$TAG"
SNAP="$RUN/snapshot"
mkdir -p "$RUN"
"$PYTHON_BIN" - "$ROOT" "$SNAP" "$DATA_DIR" "$SEED_TAGS" "${INSTS[@]}" <<'PY'
import hashlib, json, pathlib, shutil, sys
root, snap, data, tags, *instances = map(pathlib.Path, sys.argv[1:])
tags_text = str(tags)
if snap.exists(): raise SystemExit("snapshot already exists")
(snap / "tools").mkdir(parents=True)
(snap / "solver").mkdir()
(snap / "data").mkdir()
for relative in ("tools/run_branch_price_probe.py", "tools/branch_price_check.py", "tools/nfp_pricer.py", "tools/nfp_oracle.py", "tools/branch_price_probe.sbatch", "tools/submit_branch_price_probe.sh", "tools/test_branch_price_check.py", "tools/test_nfp_pricer.py", "tools/test_nfp_oracle.py"):
    source = root / relative
    shutil.copy2(source, snap / relative)
shutil.copy2(root / "solver/utils.py", snap / "solver/utils.py")
seed_manifest = {}
for instance_path in instances:
    instance = str(instance_path)
    shutil.copy2(data / f"prob_{instance}.json", snap / "data" / f"prob_{instance}.json")
    paths = []
    for tag in tags_text.split(","):
        candidates = sorted(root.joinpath("runs").glob(tag + "*/prob_" + instance + ".sol.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates: raise SystemExit(f"no seed for tag={tag!r}, instance={instance}")
        source = candidates[0]
        target = snap / "seeds" / instance / (tag + ".sol.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        paths.append({"tag": tag, "source": str(source), "snapshot": str(target.relative_to(snap)), "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
    if len(paths) < 2: raise SystemExit("at least two seed solutions are required")
    seed_manifest[instance] = paths
files = {}
for path in sorted(p for p in snap.rglob("*") if p.is_file()):
    files[str(path.relative_to(snap))] = hashlib.sha256(path.read_bytes()).hexdigest()
(snap / "MANIFEST.json").write_text(json.dumps({"files": files, "seeds": seed_manifest}, sort_keys=True, indent=2) + "\n")
PY
MANIFEST_HASH="$("$PYTHON_BIN" - "$SNAP/MANIFEST.json" <<'PY'
import hashlib, pathlib, sys
print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)"

"$PYTHON_BIN" - "$RUN/META.json" "$TAG" "$PYTHON_BIN" "$DATA_DIR" "$SEED_TAGS" "$INSTANCES" "$TIME_BUDGET" "$BEAM_WIDTH" "$MAX_STATES" "$MAX_CANDIDATES" "$MAX_ROOT_ROUNDS" "$MIN_GAIN_P4" "$MIN_GAIN_P5" "$PARTITION" "$MANIFEST_HASH" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
keys = ("tag", "python_bin", "data_dir", "seed_tags", "instances", "time_budget", "beam_width", "max_states", "max_candidates", "max_root_rounds", "min_gain_p4", "min_gain_p5", "partition", "snapshot_manifest_sha256")
path.write_text(json.dumps(dict(zip(keys, sys.argv[2:])), sort_keys=True, indent=2) + "\n")
PY
chmod -R a-w "$SNAP"

JOBS=()
for instance in "${INSTS[@]}"; do
  if [[ "$instance" == 4 ]]; then
    MIN_GAIN="$MIN_GAIN_P4"
  else
    MIN_GAIN="$MIN_GAIN_P5"
  fi
  SEEDS=("$SNAP/seeds/$instance"/*.sol.json)
  # Re-query under the shared-filesystem lock immediately before every submit.
  # This closes races among all users of this harness and expands array tasks.
  QUEUED_COUNT="$(queue_count)"
  (( QUEUED_COUNT + 1 <= 8 )) || {
    echo "refusing submission: $QUEUED_COUNT queued/running tasks plus 1 exceeds 8" >&2
    exit 2
  }
  jobid="$(sbatch --parsable --job-name="branch-price-$TAG" --partition="$PARTITION" --array=0-0 \
    --output="$RUN/prob_${instance}.log" --error="$RUN/prob_${instance}.log" \
    "$SNAP/tools/branch_price_probe.sbatch" "$SNAP" "$PYTHON_BIN" "$instance" "$TIME_BUDGET" \
    "$BEAM_WIDTH" "$MAX_STATES" "$MAX_CANDIDATES" "$MAX_ROOT_ROUNDS" "$MIN_GAIN" "${SEEDS[@]}")"
  JOBS+=("$jobid")
done
printf 'submitted tag=%s job_ids=%s\n' "$TAG" "${JOBS[*]}"
printf 'collect/freeze: chmod -R a-w -- %q\n' "$RUN"
