#!/bin/bash
#SBATCH -J jc1short
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
# The dilution data reframes jointcon: its basin's ceiling equals the normal
# one's, but it REACHES ~29M by ~400s where the normal path needs ~900s. The
# finals' TLs are "a few minutes to half an hour" -- on short-TL congested
# instances the speed difference IS score. Paired short-TL screen: P6 at TL
# 300 and 450, base then jc1 on the same cpuset.
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/jc1short
run() {
  OGC_SEED=$3 OMP_NUM_THREADS=4 taskset -c $4 $PY -u - "$1" "$2" <<'PYEOF'
import json,os,sys,time
d,tl=sys.argv[1],float(sys.argv[2])
sys.path.insert(0,d)
import myalgorithm as ma
prob=json.load(open("data/preliminary_test/prob_6.json"))
t=time.time(); sol=ma.algorithm(prob,timelimit=tl); w=time.time()-t
r=ma._check(prob,sol)
print("%-10s tl=%s seed %s  obj %14s  feasible=%s  %5.1fs"%(d,sys.argv[2],os.environ["OGC_SEED"],
  format(r.get("objective",0) if r else 0,",.0f"), r.get("feasible") if r else None, w))
PYEOF
}
i=0
for arg in "300 7" "300 11" "300 23" "450 7" "450 11" "450 23"; do
  set -- $arg
  c=$(( (i % 4) * 8 ))
  ( run solver     $1 $2 $c-$((c+7)) 2>/dev/null
    run builds/jc1 $1 $2 $c-$((c+7)) 2>/dev/null ) > runs/jc1short/tl$1-s$2.txt &
  i=$((i+1))
  if [ $((i % 4)) -eq 0 ]; then wait; fi
done
wait
for f in runs/jc1short/*.txt; do cat $f; done
