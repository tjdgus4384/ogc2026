#!/usr/bin/env bash
#SBATCH -J ecap2swp
#SBATCH -N 1
#SBATCH -c 32
#SBATCH --mem=48G
#SBATCH -t 01:00:00
#
# How many blocks should the anneal-long punctuality lock force, and does it
# move anything at all? Two seeds, because one draw cannot resolve anything
# below ~10% on P5 (08-13 power table). k=0 skips the branch and is the A/A.
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
CH='
import json,os,sys,pathlib
REPO=pathlib.Path(os.environ["SLURM_SUBMIT_DIR"]).resolve()
sys.path.insert(0,str(REPO/"builds"/"ecap3"))
import myalgorithm as ma
prob=json.load(open(REPO/"data"/"preliminary_test"/(sys.argv[1]+".json")))
r=ma._check(prob,ma.algorithm(prob,timelimit=float(sys.argv[2])))
print("%-8s s%-3s ECAP2=%-3s %12d  %s"%(sys.argv[1],os.environ.get("OGC_SEED"),
      os.environ.get("OGC_ECAP2","0"),r["objective"],r["feasible"]))
'
for k in 0 2 5 10; do
  for s in 7 11; do
    ( OGC_SEED=$s OGC_ECAP2=$k "$PY" -c "$CH" prob_5 600 ) &
  done
done
wait
