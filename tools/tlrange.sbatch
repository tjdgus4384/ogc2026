#!/usr/bin/env bash
#SBATCH -J tlrange
#SBATCH -N 1
#SBATCH -c 32
#SBATCH --mem=48G
#SBATCH -t 02:00:00
#
# The finals' time limits are not published. The driver has three TL-dependent
# branches (rounds at sec>110, _pop_here at sec/n<=1.6, the 30% slice share),
# and we have only ever run inside the preliminary range of 60-900s. Confirm
# the solver stays feasible, places every block and shows no catastrophe well
# outside that band.
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
CH='
import json,os,sys,pathlib,time
REPO=pathlib.Path(os.environ["SLURM_SUBMIT_DIR"]).resolve()
sys.path.insert(0,str(REPO/"solver"))
import myalgorithm as ma
prob=json.load(open(REPO/"data"/"preliminary_test"/(sys.argv[1]+".json")))
n=len(prob["blocks"]); tl=float(sys.argv[2])
t0=time.time(); sol=ma.algorithm(prob,timelimit=tl); el=time.time()-t0
r=ma._check(prob,sol)
ops=sum(1 for L in sol["operations"].values() for o in L if o["type"]=="ENTRY")
print("%-8s TL%-5.0f placed=%3d/%3d obj=%12d feasible=%-5s wall=%6.1f margin=%+5.1f"%(
      sys.argv[1],tl,ops,n,r["objective"],r["feasible"],el,tl-el))
'
for tl in 20 45 120 1200 1800; do
  ( OGC_SEED=7 "$PY" -c "$CH" prob_5 $tl ) &
  ( OGC_SEED=7 "$PY" -c "$CH" prob_4 $tl ) &
done
wait
