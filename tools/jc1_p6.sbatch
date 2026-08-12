#!/bin/bash
#SBATCH -J jc1p6
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/jc1
i=0
for s in 7 11 23; do
  OGC_DIAG=1 OGC_SEED=$s OMP_NUM_THREADS=4 taskset -c $((i*8))-$((i*8+7)) \
    $PY -u - <<'PYEOF' > runs/jc1/jc1fix-p6-s$s.txt 2>runs/jc1/jc1fix-p6-s$s.err &
import json,os,sys,time
sys.path.insert(0,'builds/jc1')
import myalgorithm as ma
prob=json.load(open("data/preliminary_test/prob_6.json"))
t=time.time(); sol=ma.algorithm(prob,timelimit=900); w=time.time()-t
r=ma._check(prob,sol)
print("jc1 prob_6 seed %s  obj %14s  feasible=%s  %5.1fs"%(os.environ["OGC_SEED"],
  format(r.get("objective",0) if r else 0,",.0f"), r.get("feasible") if r else None, w))
PYEOF
  i=$((i+1))
done
wait
for s in 7 11 23; do cat runs/jc1/jc1fix-p6-s$s.txt; grep jointcon runs/jc1/jc1fix-p6-s$s.err; done
