#!/usr/bin/env bash
#SBATCH -J rsviter
#SBATCH -N 1
#SBATCH -c 16
#SBATCH --mem=32G
#SBATCH -t 01:00:00
#
# Attribution check: does the guard win by PLACING better, or by making each
# repair cheaper and thus buying more iterations in the same wall clock?
# Both arms use the full TL (587.4s vs 587.3s median), so wall time cannot
# separate them -- iteration counts can.
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
run() {
  OGC_DIAG=1 OGC_RSV=$2 OGC_SEED=$3 "$PY" - "$1" "$4" <<'EOF' 2>&1 | grep -E "portfolio|slice" | sed "s/^/[rsv=$2 s$3] /"
import json,os,sys,pathlib
REPO=pathlib.Path(os.environ["SLURM_SUBMIT_DIR"]).resolve()
sys.path.insert(0,str(REPO/"builds"/"rsv"))
import myalgorithm as ma
prob=json.load(open(REPO/"data"/"preliminary_test"/f"{sys.argv[1]}.json"))
sol=ma.algorithm(prob,timelimit=float(sys.argv[2]))
r=ma._check(prob,sol)
print("RESULT objective=%d"%r["objective"], file=sys.stderr)
EOF
}
export -f run; export REPO PY
for s in 7 11; do
  run prob_5 0 $s 600 &
  run prob_5 1 $s 600 &
done
wait
