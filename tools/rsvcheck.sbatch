#!/usr/bin/env bash
#SBATCH -J rsvchk
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
for p in prob_3 prob_4 prob_5 prob_6; do
  F="$REPO/data/preliminary_test/$p.json"
  S=$(OGC_RSV=0 "$PY" -c "$CH" "$REPO/solver"      "$F" | sed -n 's/^@@//p')
  Z=$(OGC_RSV=0 "$PY" -c "$CH" "$REPO/builds/rsv"  "$F" | sed -n 's/^@@//p')
  O=$(OGC_RSV=1 "$PY" -c "$CH" "$REPO/builds/rsv"  "$F" | sed -n 's/^@@//p')
  [ "$S" = "$Z" ] && AA="A/A OK" || AA="A/A MISMATCH"
  [ "$Z" = "$O" ] && FIRE="가드 미발동(보드 동일)" || FIRE="가드 발동(보드 변함)"
  echo "$p  $AA  |  $FIRE"
  echo "   shipped/off: $Z"
  echo "   rsv on     : $O"
done
