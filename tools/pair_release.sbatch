#!/usr/bin/env bash
#SBATCH --job-name=popgate
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=00:55:00
# One matched AB/BA pair of the population production gate.
#   sbatch --array=K -p titan tools/pair_release.sbatch GATE_DIR
# Runs both treatments SEQUENTIALLY in this single 4-CPU/16G allocation,
# order taken from MANIFEST.json. Never overwrites existing receipts.
set -euo pipefail
GATE="$1"
PY=${OGC_PY:-/data_fast/home/tjdgus4384/priv/ogc2026/.miniforge/envs/ogc2026/bin/python}
IDX=${SLURM_ARRAY_TASK_ID:?array index required}

SPEC=$("$PY" - "$GATE" "$IDX" <<'EOF'
import json, sys
m = json.loads(open(sys.argv[1] + "/MANIFEST.json").read())
p = m["matrix"][int(sys.argv[2])]
print(json.dumps(p))
EOF
)
PAIR_ID=$(echo "$SPEC" | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["pairId"])')
INST=$(echo "$SPEC" | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["instance"])')
TL=$(echo "$SPEC" | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["tl"])')
SEED=$(echo "$SPEC" | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["seed"])')
T0=$(echo "$SPEC" | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["order"][0])')
T1=$(echo "$SPEC" | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["order"][1])')

HARNESS="$GATE/snapshot/harness/pair_release_run.py"
LOG="$GATE/pairs/$PAIR_ID"
mkdir -p "$LOG"

for PHASE in 0 1; do
    T=$([ "$PHASE" = 0 ] && echo "$T0" || echo "$T1")
    timeout --signal=TERM --kill-after=15 "$(( ${TL%.*} + 90 ))s" \
        "$PY" "$HARNESS" --gate-dir "$GATE" --pair-id "$PAIR_ID" \
        --phase "$PHASE" --treatment "$T" --instance "$INST" \
        --tl "$TL" --seed "$SEED" >> "$LOG/run.log" 2>&1
done

sacct -P -n -j "$SLURM_JOB_ID" \
    --format=JobIDRaw,State,ExitCode,ElapsedRaw,MaxRSS,AllocCPUS,NodeList \
    > "$LOG/sacct.txt" || true
echo done > "$LOG/DONE"
