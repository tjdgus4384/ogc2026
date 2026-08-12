#!/bin/bash
#SBATCH -J jc1b
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
# Budgeted jc1 vs shipped v6.2, PAIRED: each seed's two runs share a cpuset,
# base first then jc1, so the node draw cancels within the pair.
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/jc1b
run() {
  OGC_DIAG=1 OGC_SEED=$3 OMP_NUM_THREADS=4 taskset -c $4 $PY -u - "$1" "$2" <<'PYEOF'
import json,os,sys,time
d,pid=sys.argv[1],sys.argv[2]
sys.path.insert(0,d)
import myalgorithm as ma
prob=json.load(open("data/preliminary_test/%s.json"%pid))
t=time.time(); sol=ma.algorithm(prob,timelimit=900); w=time.time()-t
r=ma._check(prob,sol)
print("%-10s %s seed %s  obj %14s  feasible=%s  %5.1fs"%(d,pid,os.environ["OGC_SEED"],
  format(r.get("objective",0) if r else 0,",.0f"), r.get("feasible") if r else None, w))
PYEOF
}
i=0
for s in 7 11 23 31; do
  ( run solver     prob_6 $s $((i*8))-$((i*8+7)) 2>/dev/null
    run builds/jc1 prob_6 $s $((i*8))-$((i*8+7)) 2>runs/jc1b/s$s.err ) \
    > runs/jc1b/s$s.txt &
  i=$((i+1))
done
wait
for s in 7 11 23 31; do cat runs/jc1b/s$s.txt; grep jointcon runs/jc1b/s$s.err 2>/dev/null; done
