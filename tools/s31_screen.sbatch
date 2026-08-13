#!/bin/bash
#SBATCH -J swo1scr
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
# Deterministic paired screen of swo1 (v6.3 + squeaky-wheel seed) against
# shipped v6.3: P4 and P5 (where SWO leads at the seed level) x 2 seeds,
# full driver, official TLs, base then candidate on the same cpuset.
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/s31
run() {
  OGC_DIAG=1 OGC_SEED=$4 OMP_NUM_THREADS=4 taskset -c $5 $PY -u - "$1" "$2" "$3" <<'PYEOF' 2>&1 | grep -E "obj |\"swo\""
import json,os,sys,time
d,pid,tl=sys.argv[1],sys.argv[2],float(sys.argv[3])
sys.path.insert(0,d)
import myalgorithm as ma
prob=json.load(open("data/preliminary_test/%s.json"%pid))
t=time.time(); sol=ma.algorithm(prob,timelimit=tl); w=time.time()-t
r=ma._check(prob,sol)
print("%-11s %s seed %s  obj %14s  feasible=%s  %5.1fs"%(d,pid,os.environ["OGC_SEED"],
  format(r.get("objective",0) if r else 0,",.0f"), r.get("feasible") if r else None, w))
PYEOF
}
i=0
for arg in "prob_5 600 7" "prob_5 600 11" "prob_5 600 23" "prob_4 480 7"; do
  set -- $arg
  c=$(( i * 8 ))
  ( run solver     $1 $2 $3 $c-$((c+7))
    run builds/s31 $1 $2 $3 $c-$((c+7)) ) > runs/s31/$1-s$3.txt &
  i=$((i+1))
done
wait
for f in runs/s31/*.txt; do cat $f; done
