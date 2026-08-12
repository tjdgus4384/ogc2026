#!/bin/bash
#SBATCH -J bld
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
set -euo pipefail
export PYTHONNOUSERSITE=1
export PY="$PWD/.miniforge/envs/ogc2026/bin/python"
cd builds/pc2 && bash build.sh
cd ../..
# deterministic check: the model must stop forbidding the incumbent
for p in prob_4 prob_5; do
  OMP_NUM_THREADS=4 taskset -c 0-7 $PY -u tools/incumbent_conflict.py solver      $p 120
  OMP_NUM_THREADS=4 taskset -c 0-7 $PY -u tools/incumbent_conflict.py builds/pc2 $p 120
done
# smoke: the known optima must survive
for p in prob_1 prob_2; do
  OMP_NUM_THREADS=4 taskset -c 0-7 $PY -u - "$p" <<'PYEOF'
import json,os,sys,time
sys.path.insert(0,'builds/pc2'); os.environ["OGC_SEED"]="7"
import myalgorithm as ma
prob=json.load(open("data/preliminary_test/%s.json"%sys.argv[1]))
t=time.time(); sol=ma.algorithm(prob,timelimit=60); w=time.time()-t
r=ma._check(prob,sol)
print("   SMOKE %s  %12s  feasible=%s  %.1fs"%(sys.argv[1],
      format(r.get("objective",0) if r else 0,",.0f"), r.get("feasible") if r else None, w))
PYEOF
done
