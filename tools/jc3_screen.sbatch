#!/bin/bash
#SBATCH -J jc3scr
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/jc3
run() {
  OGC_DIAG=1 OGC_SEED=$3 OMP_NUM_THREADS=4 taskset -c $4 $PY -u - "$1" "$2" <<'PYEOF' 2>&1 | grep -E "obj |jointcon"
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
for arg in "300 7" "300 11" "300 23" "300 31"; do
  set -- $arg
  c=$(( i * 8 ))
  ( run solver     $1 $2 $c-$((c+7))
    run builds/jc3 $1 $2 $c-$((c+7)) ) > runs/jc3/tl$1-s$2.txt &
  i=$((i+1))
done
wait
for f in runs/jc3/*.txt; do cat $f; done
