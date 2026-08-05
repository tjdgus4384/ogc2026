#!/usr/bin/env bash
# Submit an evaluation as whole-node Slurm jobs. NEVER run the solver on the
# gateway.
#
#   tools/submit_eval.sh <tag> <timelimit_sec|official> [partition] [instances]
#
#   tag        names the run; raw output in runs/<tag>/, frozen into
#              results/<tag>.json by tools/collect.py
#   timelimit  the value handed to algorithm(prob, timelimit)
#   partition  dept (default) or titan
#   instances  "1-40" (default), or a list like "1,2,22,28,38"
#
# Shape: each job takes ONE node and runs 10 instances in parallel, each
# pinned to 4 cores + 16 GB = the grading envelope. A full 40 is 4 jobs /
# 4 nodes and finishes in ~one instance-runtime (T300 -> ~6 min).
#
# CLUSTER BUDGET: at most 4 of our node-jobs running at once, and NO slurm
# dependency queuing -- pending dependency jobs sit in everyone's squeue.
# Run evals back to back by waiting for the previous one to finish, then
# submitting the next (the session orchestrates; the queue stays clean).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAG="${1:?usage: submit_eval.sh <tag> <timelimit_sec> [partition] [instances]}"
TL="${2:?usage: submit_eval.sh <tag> <timelimit_sec> [partition] [instances]}"
PART="${3:-dept}"
INSTSPEC="${4:-1-40}"
SOLVER="${SOLVER:-$ROOT/solver}"
PSEED="${SEED:-7}"
PERNODE=10

if [[ -e "$ROOT/results/$TAG.json" ]]; then
    echo "results/$TAG.json already exists -- pick a new tag." >&2
    echo "Frozen results are never overwritten." >&2
    exit 1
fi

RUNNING=$(squeue -h -u "$USER" 2>/dev/null | wc -l)
if (( RUNNING + 4 > 8 )); then
    echo "refusing: $RUNNING jobs queued; +4 would exceed the 8-node budget." >&2
    squeue -u "$USER" >&2
    exit 1
fi

# expand "1-40" / "3,7,22" into a list
INSTS=$(python3 - "$INSTSPEC" <<'PY'
import sys
out = []
for part in sys.argv[1].split(','):
    if '-' in part:
        a, b = part.split('-'); out += list(range(int(a), int(b)+1))
    else:
        out.append(int(part))
print(' '.join(map(str, out)))
PY
)
read -ra ALL <<< "$INSTS"

if [[ "$TL" == "official" ]]; then
    WALL=25
    TL_JSON='"official"'
    TL_MAP=', "timelimits": {"prob_1":60,"prob_2":120,"prob_3":240,"prob_4":480,"prob_5":600,"prob_6":900}'
else
    WALL=$(python3 -c "import math;print(int(math.ceil(($TL+600)/60)))")
    TL_JSON="$TL"
    TL_MAP=""
fi
mkdir -p "$ROOT/runs/$TAG"
SNAP="$ROOT/runs/$TAG/solver_snapshot"
CHECKER_SNAPSHOT="$ROOT/runs/$TAG/checker_snapshot.py"
HARNESS="$ROOT/runs/$TAG/harness_snapshot"
DATA_SNAPSHOT="$ROOT/runs/$TAG/data_snapshot"
if [[ -e "$SNAP" || -e "$CHECKER_SNAPSHOT" || -e "$HARNESS" ||
      -e "$DATA_SNAPSHOT" ]]; then
    echo "runs/$TAG already contains immutable snapshots -- pick a new tag." >&2
    exit 1
fi
SNAP_HASH=$(python3 - "$SOLVER" "$SNAP" "$ROOT/solver/utils.py" "$CHECKER_SNAPSHOT" <<'PY'
import hashlib, pathlib, shutil, sys
src, dst, checker, checker_dst = map(pathlib.Path, sys.argv[1:])
shutil.copytree(src, dst, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
shutil.copy2(checker, checker_dst)
h = hashlib.sha256()
for p in sorted(x for x in dst.rglob('*') if x.is_file()):
    rel = str(p.relative_to(dst)).encode()
    data = p.read_bytes()
    h.update(len(rel).to_bytes(8, 'big')); h.update(rel)
    h.update(len(data).to_bytes(8, 'big')); h.update(data)
print(h.hexdigest())
PY
)
CHECKER_HASH=$(python3 - "$CHECKER_SNAPSHOT" <<'PY'
import hashlib, pathlib, sys
print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)
HARNESS_HASH=$(python3 - "$HARNESS" "$ROOT/tools/eval.sbatch" "$ROOT/tools/run_instance.py" <<'PY'
import hashlib, pathlib, shutil, sys
dst, *sources = map(pathlib.Path, sys.argv[1:])
dst.mkdir()
h = hashlib.sha256()
for src in sources:
    target = dst / src.name
    shutil.copy2(src, target)
    data = target.read_bytes()
    h.update(src.name.encode()); h.update(len(data).to_bytes(8, 'big')); h.update(data)
print(h.hexdigest())
PY
)
DATA_SNAPSHOT_HASH=$(python3 - "$ROOT/data" "$DATA_SNAPSHOT" "${OGC_DATA:-train}" "$INSTS" <<'PY'
import hashlib, pathlib, sys
src, dst = map(pathlib.Path, sys.argv[1:3])
dataset, insts = sys.argv[3], sys.argv[4].split()
target = dst / dataset
target.mkdir(parents=True)
h = hashlib.sha256()
for ident in insts:
    name = f"prob_{ident}.json"
    data = (src / dataset / name).read_bytes()
    (target / name).write_bytes(data)
    h.update(name.encode()); h.update(len(data).to_bytes(8, 'big')); h.update(data)
print(h.hexdigest())
PY
)
export OGC_DATA_ROOT="$DATA_SNAPSHOT"
export OGC_DATA_SNAPSHOT_SHA256="$DATA_SNAPSHOT_HASH"
export OGC_HARNESS_SHA256="$HARNESS_HASH"
export OGC_CHECKER="$CHECKER_SNAPSHOT"

cat > "$ROOT/runs/$TAG/META.json" <<EOF
{"tag": "$TAG", "timelimit": $TL_JSON$TL_MAP, "partition": "$PART", "instances": "$INSTSPEC",
 "data": "${OGC_DATA:-train}",
 "solver_dir": "$(basename "$SOLVER")", "pseed": $PSEED,
 "solver_snapshot_sha256": "$SNAP_HASH", "checker_sha256": "$CHECKER_HASH",
 "harness_sha256": "$HARNESS_HASH",
 "data_snapshot_sha256": "$DATA_SNAPSHOT_HASH",
 "solver_commit": "$(cd "$ROOT" && git rev-parse --short HEAD)",
 "solver_dirty": $(cd "$ROOT" && git diff --quiet HEAD -- solver && echo false || echo true),
 "submitted": "$(date -Iseconds)"}
EOF

JIDS=()
for ((o = 0; o < ${#ALL[@]}; o += PERNODE)); do
    CHUNK="${ALL[@]:o:PERNODE}"
    JID=$(sbatch --parsable --partition="$PART" --time="$WALL" \
          --job-name="eval" \
          "$HARNESS/eval.sbatch" "$TAG" "$TL" "$SNAP" "$CHUNK" "$PSEED" \
          "$HARNESS/run_instance.py")
    JIDS+=("$JID")
done

echo "submitted ${#JIDS[@]} node-jobs (${JIDS[*]})  tag=$TAG  tl=$TL  partition=$PART"
echo "  watch:   squeue -u \$USER"
echo "  collect: python3 tools/collect.py $TAG"
