#!/usr/bin/env bash
#SBATCH -J rsv4null
#SBATCH -N 1
#SBATCH -c 16
#SBATCH --mem=32G
#SBATCH -t 01:00:00
#
# The whole case for rsv4 rests on "above the threshold the downside is
# structurally zero". Construction-level equality was already shown; this tests
# the FULL pipeline, where the extra computeFill work could in principle shift a
# deadline-driven trajectory. P6 (load 2.86) and P3 (never fires) must come back
# equal to the shipped solver at the same seed.
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
run() {
  OGC_SEED=$3 env $4 "$PY" - "$1" "$2" <<'EOF' 2>/dev/null
import json,os,sys,pathlib
REPO=pathlib.Path(os.environ["SLURM_SUBMIT_DIR"]).resolve()
sys.path.insert(0,sys.argv[3] if len(sys.argv)>3 else str(REPO/"solver"))
import myalgorithm as ma
prob=json.load(open(REPO/"data"/"preliminary_test"/f"{sys.argv[1]}.json"))
sol=ma.algorithm(prob,timelimit=float(sys.argv[2]))
r=ma._check(prob,sol)
print("%d"%r["objective"])
EOF
}
for spec in "prob_3 240" "prob_6 900"; do
  set -- $spec; P=$1; TL=$2
  for S in 7 11; do
    A=$(OGC_SEED=$S OGC_RSV=0 "$PY" -c "
import json,os,sys,pathlib
REPO=pathlib.Path(os.environ['SLURM_SUBMIT_DIR']).resolve()
sys.path.insert(0,str(REPO/'solver'))
import myalgorithm as ma
prob=json.load(open(REPO/'data'/'preliminary_test'/'$P.json'))
print('%d'%ma._check(prob,ma.algorithm(prob,timelimit=$TL))['objective'])" 2>/dev/null)
    B=$(OGC_SEED=$S OGC_RSV=1 OGC_RSVMAXLOAD=160 "$PY" -c "
import json,os,sys,pathlib
REPO=pathlib.Path(os.environ['SLURM_SUBMIT_DIR']).resolve()
sys.path.insert(0,str(REPO/'builds'/'rsv4'))
import myalgorithm as ma
prob=json.load(open(REPO/'data'/'preliminary_test'/'$P.json'))
print('%d'%ma._check(prob,ma.algorithm(prob,timelimit=$TL))['objective'])" 2>/dev/null)
    if [ "$A" = "$B" ]; then echo "$P s$S TL$TL  IDENTICAL  $A"
    else echo "$P s$S TL$TL  DIFFER  shipped=$A  gated=$B"; fi
  done
done
