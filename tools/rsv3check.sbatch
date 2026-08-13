#!/usr/bin/env bash
#SBATCH -J rsv3chk
#SBATCH -N 1
#SBATCH -c 8
#SBATCH --mem=16G
#SBATCH -t 00:30:00
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
  OFF=$(OGC_RSV=0 "$PY" -c "$CH" "$REPO/builds/rsv3" "$F" | sed -n 's/^@@//p')
  BASE=$(OGC_RSV=1 "$PY" -c "$CH" "$REPO/builds/rsv" "$F" | sed -n 's/^@@//p')
  R0=$(OGC_RSV=1 OGC_RSVRELIEF=0 "$PY" -c "$CH" "$REPO/builds/rsv3" "$F" | sed -n 's/^@@//p')
  R1=$(OGC_RSV=1 OGC_RSVRELIEF=1 "$PY" -c "$CH" "$REPO/builds/rsv3" "$F" | sed -n 's/^@@//p')
  A=$([ "$SHIP" = "$OFF" ] && echo "A/A OK" || echo "A/A MISMATCH")
  B=$([ "$R0" = "$BASE" ] && echo "relief0=rsv OK" || echo "relief0!=rsv MISMATCH")
  C=$([ "$R1" = "$R0" ] && echo "relief 무효과" || echo "relief 작동")
  echo "$p  $A | $B | $C"
  echo "    relief=1: $R1"
  echo "    relief=0: $R0"
done
