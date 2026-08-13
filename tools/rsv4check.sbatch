#!/usr/bin/env bash
#SBATCH -J rsv4chk
#SBATCH -N 1
#SBATCH -c 8
#SBATCH --mem=16G
#SBATCH -t 00:30:00
#
# The load gate must be a clean switch: above the threshold the run has to be
# byte-identical to the shipped solver (that is what bounds the downside at
# zero), and below it identical to rsv. Anything in between means the gate is
# doing something of its own, which was never measured.
#
#   P4 load 1.26, P5 1.36  -> below 150, expect "same as rsv"
#   P6 load 2.19           -> above 150, expect "same as shipped"
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
CH='
import json,os,sys
sys.path.insert(0,sys.argv[1])
import myalgorithm as ma
prob=json.load(open(sys.argv[2])); n=len(prob["blocks"])
eng,D,P=ma._fresh(prob,4); out=[]
for key in [lambda i:(D[i],-P[i]),lambda i:(D[i]-P[i],-P[i]),lambda i:(-P[i],D[i])]:
    order=sorted(range(n),key=key)
    bad=eng.reconstruct_ordered([-1]*n,order,False,7)
    out.append(None if bad>=0 else eng.objective()[3])
print("@@"+json.dumps(out))
'
for p in prob_4 prob_5 prob_6; do
  F="$REPO/data/preliminary_test/$p.json"
  SHIP=$(OGC_RSV=0 "$PY" -c "$CH" "$REPO/solver" "$F" | sed -n 's/^@@//p')
  RSV=$(OGC_RSV=1 "$PY" -c "$CH" "$REPO/builds/rsv" "$F" | sed -n 's/^@@//p')
  G=$(OGC_RSV=1 OGC_RSVMAXLOAD=150 "$PY" -c "$CH" "$REPO/builds/rsv4" "$F" | sed -n 's/^@@//p')
  if [ "$G" = "$SHIP" ]; then echo "$p  gate150 -> SHIPPED (guard stood down)"
  elif [ "$G" = "$RSV" ]; then echo "$p  gate150 -> RSV (guard active)"
  else
    echo "$p  gate150 -> NEITHER  !! the gate is doing something of its own"
    echo "    gate: $G"
    echo "    ship: $SHIP"
    echo "    rsv : $RSV"
  fi
done
