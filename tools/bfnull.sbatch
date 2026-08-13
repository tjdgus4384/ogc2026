#!/usr/bin/env bash
#SBATCH -J bfnull
#SBATCH -N 1
#SBATCH -c 8
#SBATCH --mem=16G
#SBATCH -t 00:30:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
CH='
import json,os,sys,pathlib
sys.path.insert(0,sys.argv[1])
import myalgorithm as ma
prob=json.load(open(sys.argv[2]))
n=len(prob["blocks"])
eng,D,P=ma._fresh(prob,4)
out=[]
for k,key in enumerate([lambda i:(D[i],-P[i]),lambda i:(D[i]-P[i],-P[i]),lambda i:(-P[i],D[i])]):
    order=sorted(range(n),key=key)
    bad=eng.reconstruct_ordered([-1]*n,order,False,7)
    out.append(None if bad>=0 else eng.objective()[3])
print("@@"+json.dumps(out))
'
for p in prob_4 prob_5 prob_6; do
  F="$REPO/data/preliminary_test/$p.json"
  A=$(OGC_BESTFIT=0 "$PY" -c "$CH" "$REPO/solver"     "$F" | sed -n 's/^@@//p')
  B=$(OGC_BESTFIT=0 "$PY" -c "$CH" "$REPO/builds/bf1" "$F" | sed -n 's/^@@//p')
  if [ "$A" = "$B" ]; then echo "$p  IDENTICAL  $A"; else echo "$p  DIFFER"; echo "   solver: $A"; echo "   bf1off: $B"; fi
done
