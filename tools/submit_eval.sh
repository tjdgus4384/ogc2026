#!/usr/bin/env bash
# Submit an evaluation as whole-node Slurm jobs. NEVER run the solver on the
# gateway.
#
#   tools/submit_eval.sh <tag> <timelimit_sec> [partition] [instances]
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
# CLUSTER BUDGET: at most 4 of our node-jobs running at once. To queue another
# eval behind a running one, pass DEPEND="<jobid> <jobid> ..." -- it submits
# with --dependency=afterany and waits its turn instead of stacking nodes.
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

if [[ -z "${DEPEND:-}" ]]; then
    RUNNING=$(squeue -h -u "$USER" -t RUNNING,CONFIGURING 2>/dev/null | wc -l)
    if (( RUNNING >= 4 )); then
        echo "refusing: $RUNNING of our jobs already running (budget 4)." >&2
        echo "Pass DEPEND=\"<jobids>\" to chain behind them, or wait." >&2
        squeue -u "$USER" >&2
        exit 1
    fi
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

WALL=$(python3 -c "import math;print(int(math.ceil(($TL+600)/60)))")
mkdir -p "$ROOT/runs/$TAG"

cat > "$ROOT/runs/$TAG/META.json" <<EOF
{"tag": "$TAG", "timelimit": $TL, "partition": "$PART", "instances": "$INSTSPEC",
 "solver_dir": "$(basename "$SOLVER")", "pseed": $PSEED,
 "solver_commit": "$(cd "$ROOT" && git rev-parse --short HEAD)",
 "solver_dirty": $(cd "$ROOT" && git diff --quiet HEAD -- solver && echo false || echo true),
 "submitted": "$(date -Iseconds)"}
EOF

DEP_FLAG=()
if [[ -n "${DEPEND:-}" ]]; then
    DEP_FLAG=(--dependency="afterany:$(echo "$DEPEND" | tr ' ' ':')")
fi

JIDS=()
for ((o = 0; o < ${#ALL[@]}; o += PERNODE)); do
    CHUNK="${ALL[@]:o:PERNODE}"
    JID=$(sbatch --parsable --partition="$PART" --time="$WALL" \
          --job-name="ogc-$TAG" "${DEP_FLAG[@]}" \
          "$ROOT/tools/eval.sbatch" "$TAG" "$TL" "$SOLVER" "$CHUNK" "$PSEED")
    JIDS+=("$JID")
done

echo "submitted ${#JIDS[@]} node-jobs (${JIDS[*]})  tag=$TAG  tl=${TL}s  partition=$PART"
echo "  watch:   squeue -u \$USER"
echo "  chain:   DEPEND=\"${JIDS[*]}\" tools/submit_eval.sh <next> ..."
echo "  collect: python3 tools/collect.py $TAG"
