#!/usr/bin/env bash
# Submit a full-40 evaluation to Slurm. NEVER run the solver on the gateway.
#
#   tools/submit_eval.sh <tag> <timelimit_seconds> [partition] [instances]
#
#   tag        names the run; results land in runs/<tag>/ and are frozen into
#              results/<tag>.json by tools/collect.py
#   timelimit  the value handed to algorithm(prob, timelimit) -- the number the
#              grading server would use. Typical hidden-instance limits are
#              "a few minutes to half an hour" (problem_statement.md §3.2).
#   partition  dept (default) or titan. Both are CPU nodes with no oversubscribe.
#   instances  sbatch array spec, default 1-40. e.g. "1,2,22,28,38" for a probe.
#
# The Slurm wall limit is set to the algorithm limit + 10 min of slack, so a
# hung instance is killed by Slurm rather than sitting on a node forever.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAG="${1:?usage: submit_eval.sh <tag> <timelimit_sec> [partition] [instances]}"
TL="${2:?usage: submit_eval.sh <tag> <timelimit_sec> [partition] [instances]}"
PART="${3:-dept}"
ARRAY="${4:-1-40}"
SOLVER="${SOLVER:-$ROOT/solver}"

if [[ -e "$ROOT/results/$TAG.json" ]]; then
    echo "results/$TAG.json already exists -- pick a new tag." >&2
    echo "Frozen results are never overwritten; that rule is what makes" >&2
    echo "cross-version comparison meaningful." >&2
    exit 1
fi

WALL=$(python3 -c "import math;print(int(math.ceil(($TL+600)/60)))")
mkdir -p "$ROOT/runs/$TAG"

cat > "$ROOT/runs/$TAG/META.json" <<EOF
{"tag": "$TAG", "timelimit": $TL, "partition": "$PART", "array": "$ARRAY",
 "solver_dir": "'$(basename "$SOLVER")'", "solver_commit": "$(cd "$ROOT" && git rev-parse --short HEAD)",
 "solver_dirty": $(cd "$ROOT" && git diff --quiet HEAD -- solver && echo false || echo true),
 "submitted": "$(date -Iseconds)"}
EOF

JID=$(TAG="$TAG" TL="$TL" SOLVER="$SOLVER" sbatch --parsable \
        --partition="$PART" --array="$ARRAY" --time="${WALL}" \
        "$ROOT/tools/eval.sbatch")

echo "submitted array job $JID  tag=$TAG  tl=${TL}s  partition=$PART  array=$ARRAY"
echo "  watch:   squeue -j $JID"
echo "  collect: python3 tools/collect.py $TAG"
