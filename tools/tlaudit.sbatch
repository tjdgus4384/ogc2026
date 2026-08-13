#!/bin/bash
#SBATCH -J tlaudit
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
# Finals robustness audit of the SHIPPED v6.3 across the disclosed TL range
# ("a few minutes to half an hour"), with the memetic path deliberately
# included (n>=200 at TL 180-300). Checks, per run: does the start floor
# arrive, does the mid-run promotion fire, is the answer checker-feasible,
# and does wall time stay under TL (an overrun scores as a crash).
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/tlaudit
i=0
for arg in "prob_5 180" "prob_5 300" "prob_3 240" "prob_6 180"; do
  set -- $arg
  c=$((i*8)); OGC_DIAG=1 OGC_SEED=7 OMP_NUM_THREADS=4 taskset -c $c-$((c+7)) \
    $PY -u - "$1" "$2" > runs/tlaudit/$1-$2.txt 2>&1 <<'PYEOF' &
import json,os,sys,time
pid,tl=sys.argv[1],float(sys.argv[2])
sys.path.insert(0,'solver')
import myalgorithm as ma
prob=json.load(open("data/preliminary_test/%s.json"%pid))
t=time.time(); sol=ma.algorithm(prob,timelimit=tl); w=time.time()-t
r=ma._check(prob,sol)
print("RESULT %s tl=%.0f  obj %14s  feasible=%s  wall %.1fs  margin %+.1fs"%(
  pid, tl, format(r.get("objective",0) if r else 0,",.0f"),
  r.get("feasible") if r else None, w, tl-w))
PYEOF
  i=$((i+1))
done
wait
for f in runs/tlaudit/*.txt; do echo "##### $(basename $f)"; grep -E "RESULT|floor|pop" $f; done
