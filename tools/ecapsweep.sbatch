#!/usr/bin/env bash
#SBATCH -J ecapswp
#SBATCH -N 1
#SBATCH -c 32
#SBATCH --mem=48G
#SBATCH -t 01:00:00
#
# How many blocks should the anneal-long punctuality lock force? k=0 is the
# shipped path (the branch never runs), so it doubles as the A/A check.
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
CH='
import json,os,sys,pathlib
REPO=pathlib.Path(os.environ["SLURM_SUBMIT_DIR"]).resolve()
sys.path.insert(0,str(REPO/"builds"/"ecap"))
import myalgorithm as ma
prob=json.load(open(REPO/"data"/"preliminary_test"/(sys.argv[1]+".json")))
r=ma._check(prob,ma.algorithm(prob,timelimit=float(sys.argv[2])))
print("%-8s ECAP=%-3s %12d  feasible=%s"%(sys.argv[1],os.environ.get("OGC_ECAP","0"),r["objective"],r["feasible"]))
'
for k in 0 1 3 8; do
  ( OGC_SEED=7 OGC_ECAP=$k "$PY" -c "$CH" prob_4 480 ) &
  ( OGC_SEED=7 OGC_ECAP=$k "$PY" -c "$CH" prob_5 600 ) &
done
wait
