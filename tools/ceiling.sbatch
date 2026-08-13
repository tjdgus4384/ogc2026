#!/usr/bin/env bash
#SBATCH -J ceiling
#SBATCH -N 1
#SBATCH -c 32
#SBATCH --mem=48G
#SBATCH -t 04:00:00
#
# What is OUR ARCHITECTURE's ceiling, independent of any candidate?
#
# Every diagnostic we own describes our own local optimum, so none of them can
# say whether a better board is reachable. The one ground truth we can generate
# without another team's solution is our own solver given a budget it will never
# see in competition. If 10x the official TL still lands far above the target,
# the gap is architectural and no rule or parameter can close it; if it closes,
# the gap is search efficiency and the whole search should be re-tuned.
#
# Prediction on record BEFORE the run (log fit from P6 TL300 vs TL900, 3.6%
# per 3x): 10x buys ~7.5%, so P5 ~9.7M against a 7.72M target and P4 ~2.80M
# against 1.91M. A result materially better than that refutes the log fit.
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
run() {
  OGC_SEED=$2 "$PY" - "$1" "$2" "$3" <<'EOF' 2>&1 | sed "s/^/[$1 s$2 TL$3] /"
import json,os,sys,time,pathlib
REPO=pathlib.Path(os.environ.get("SLURM_SUBMIT_DIR",".")).resolve()
sys.path.insert(0,str(REPO/"solver"))
import myalgorithm as ma
pid,seed,tl=sys.argv[1],int(sys.argv[2]),float(sys.argv[3])
prob=json.load(open(REPO/"data"/"preliminary_test"/f"{pid}.json"))
t0=time.time(); sol=ma.algorithm(prob,timelimit=tl); el=time.time()-t0
r=ma._check(prob,sol)
print(f"{pid} seed {seed} TL {tl:.0f}: objective {r.get('objective'):,.0f}  feasible={r.get('feasible')}  wall {el:.0f}s")
EOF
}
export -f run; export REPO PY
for s in 7 11 13; do run prob_5 $s 6000 & done
for s in 7 11 13; do run prob_4 $s 4800 & done
wait
