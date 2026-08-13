#!/usr/bin/env bash
#SBATCH -J extremes
#SBATCH -N 1
#SBATCH -c 32
#SBATCH --mem=48G
#SBATCH -t 01:00:00
#
# Finals robustness at the covariate extremes. Our four preliminary boards span
# a narrow band (load 0.82-2.86, n 150-250), and the P5 floor regression showed
# what that hides. These are the eight most extreme instances we hold: load
# 0.41-5.41, n 100-300. Every one must come back checker-feasible with no
# catastrophe, at the official-scaled TL.
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
CH='
import json,os,sys,pathlib
REPO=pathlib.Path(os.environ["SLURM_SUBMIT_DIR"]).resolve()
sys.path.insert(0,str(REPO/"solver"))
import myalgorithm as ma
prob=json.load(open(REPO/"data"/sys.argv[1]/(sys.argv[2]+".json")))
n=len(prob["blocks"])
ma._make_floor(prob,n,20.0); fl = ma._FLOOR is not None
import time
t0=time.time(); sol=ma.algorithm(prob,timelimit=float(sys.argv[3])); el=time.time()-t0
r=ma._check(prob,sol)
ops=sum(1 for L in sol["operations"].values() for o in L if o["type"]=="ENTRY")
print("%-8s %-12s floor=%-5s placed=%3d/%3d obj=%14d feasible=%-5s wall=%5.1f"%(
      sys.argv[2],sys.argv[1],fl,ops,n,r["objective"],r["feasible"],el))
'
for spec in "train_final prob_25" "train_final prob_36" "train_final prob_13" \
            "train prob_16" "train prob_17" "train prob_18" "train prob_3" "train prob_2"; do
  set -- $spec
  ( OGC_SEED=7 "$PY" -c "$CH" $1 $2 600 ) &
done
wait
