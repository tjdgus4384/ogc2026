#!/usr/bin/env bash
#SBATCH -J softswp
#SBATCH -N 1
#SBATCH -c 32
#SBATCH --mem=48G
#SBATCH -t 01:00:00
#
# A/A first: with no cap set, the soft-cap wrapper must reproduce the shipped
# solver exactly (it only branches when entryCap is present). Then the sweep.
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
CH='
import json,os,sys,pathlib
REPO=pathlib.Path(os.environ["SLURM_SUBMIT_DIR"]).resolve()
sys.path.insert(0,sys.argv[3])
import myalgorithm as ma
prob=json.load(open(REPO/"data"/"preliminary_test"/(sys.argv[1]+".json")))
r=ma._check(prob,ma.algorithm(prob,timelimit=float(sys.argv[2])))
print("%-8s s%-3s SOFT=%-2s ECAP=%-3s %12d %s"%(sys.argv[1],os.environ.get("OGC_SEED"),
      os.environ.get("OGC_SOFTCAP","0"),os.environ.get("OGC_ECAP2","0"),r["objective"],r["feasible"]))
'
for s in 7 11; do
  ( OGC_SEED=$s "$PY" -c "$CH" prob_5 600 "$REPO/solver" ) &
  ( OGC_SEED=$s OGC_SOFTCAP=0 OGC_ECAP2=0 "$PY" -c "$CH" prob_5 600 "$REPO/builds/soft" ) &
  ( OGC_SEED=$s OGC_SOFTCAP=1 OGC_ECAP2=0 "$PY" -c "$CH" prob_5 600 "$REPO/builds/soft" ) &
  for k in 3 8; do
    ( OGC_SEED=$s OGC_SOFTCAP=1 OGC_ECAP2=$k "$PY" -c "$CH" prob_5 600 "$REPO/builds/soft" ) &
  done
done
wait
