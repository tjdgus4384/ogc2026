#!/bin/bash
#SBATCH -J jc1scr
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
# Deterministic screen of jc1 (v6.2 python + pc2 kernel + jointcon start
# hedge) against shipped v6.2. Full driver, official TLs. P6 x 3 seeds is the
# treatment; P4 is the no-fire control (saturation 0.796 < 0.9 gate, so jc1
# must behave identically up to kernel-neutral pc2 noise); P1/P2 smoke.
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/jc1
run() {  # dir inst tl seed cpu
  OGC_DIAG=1 OGC_SEED=$4 OMP_NUM_THREADS=4 taskset -c $5 $PY -u - "$1" "$2" "$3" <<'PYEOF'
import json,os,sys,time
d,pid,tl=sys.argv[1],sys.argv[2],float(sys.argv[3])
sys.path.insert(0,d)
import myalgorithm as ma
prob=json.load(open("data/preliminary_test/%s.json"%pid))
t=time.time(); sol=ma.algorithm(prob,timelimit=tl); w=time.time()-t
r=ma._check(prob,sol)
print("%s %s seed %s  obj %14s  feasible=%s  %5.1fs"%(d,pid,os.environ["OGC_SEED"],
  format(r.get("objective",0) if r else 0,",.0f"), r.get("feasible") if r else None, w))
PYEOF
}
i=0
for s in 7 11 23; do
  ( run solver     prob_6 900 $s $((i*8))-$((i*8+3)) 2>runs/jc1/base-p6-s$s.err
    run builds/jc1 prob_6 900 $s $((i*8+4))-$((i*8+7)) 2>runs/jc1/jc1-p6-s$s.err ) \
    > runs/jc1/p6-s$s.txt &
  i=$((i+1))
done
( run builds/jc1 prob_4 480 7 24-27 2>runs/jc1/jc1-p4.err
  run builds/jc1 prob_1 60 7 24-27 2>/dev/null
  run builds/jc1 prob_2 60 7 28-31 2>/dev/null ) > runs/jc1/control.txt &
wait
for f in runs/jc1/p6-s7.txt runs/jc1/p6-s11.txt runs/jc1/p6-s23.txt runs/jc1/control.txt; do
  echo "##### $f"; cat $f
done
echo "##### jointcon diags:"; grep -h jointcon runs/jc1/*.err 2>/dev/null
