#!/bin/bash
#SBATCH -J smokef
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
for d in solver builds/floor1; do
  echo "##### $d"
  for p in prob_1 prob_2; do
    OMP_NUM_THREADS=4 taskset -c 0-7 $PY -u - "$d" "$p" <<'PYEOF'
import json,os,sys,time,pathlib
d,p=sys.argv[1],sys.argv[2]
sys.path.insert(0,d); os.environ["OGC_SEED"]="7"
import myalgorithm as ma
prob=json.load(open("data/preliminary_test/%s.json"%p))
t=time.time(); sol=ma.algorithm(prob,timelimit=60); w=time.time()-t
n=len(prob["blocks"]); eng,D,P=ma._fresh(prob,4); st=[0]*(n*7)
for day,ops in sol["operations"].items():
    for op in ops:
        if op["type"]=="ENTRY":
            i=op["block_id"]; st[i*7:i*7+7]=[op["bay_id"],op["orient_idx"],op["x"],op["y"],int(day),int(day)+P[i],1]
eng.set_solution(st); eng.build_occ(eng.horizon()); o=eng.objective()
r=ma._check(prob,sol)
print("   %s  %12s  Z1=%.0f  feasible=%s  %.1fs"%(p,format(o[3],",.0f"),o[0],r.get("feasible") if r else None,w))
PYEOF
  done
done
