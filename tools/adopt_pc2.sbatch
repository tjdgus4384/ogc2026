#!/bin/bash
#SBATCH -J adoptpc2
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
set -euo pipefail
export PYTHONNOUSERSITE=1
export PY="$PWD/.miniforge/envs/ogc2026/bin/python"
cd solver && bash build.sh && cd ..
mkdir -p runs/adopt2
i=0
for arg in "prob_3 240" "prob_4 480" "prob_5 600" "prob_6 900"; do
  set -- $arg
  c=$((i*8)); OMP_NUM_THREADS=4 taskset -c $c-$((c+7)) \
    $PY -u tools/incumbent_conflict.py solver $1 $2 > runs/adopt2/$1.txt 2>&1 &
  i=$((i+1))
done
wait
for p in prob_3 prob_4 prob_5 prob_6; do cat runs/adopt2/$p.txt; done
for p in prob_1 prob_2; do
  OMP_NUM_THREADS=4 taskset -c 0-7 $PY -u - "$p" <<'PYEOF'
import json,os,sys,time
sys.path.insert(0,'solver'); os.environ["OGC_SEED"]="7"
import myalgorithm as ma
prob=json.load(open("data/preliminary_test/%s.json"%sys.argv[1]))
t=time.time(); sol=ma.algorithm(prob,timelimit=60); w=time.time()-t
r=ma._check(prob,sol)
print("   SMOKE %s  %12s  feasible=%s  %.1fs"%(sys.argv[1],
      format(r.get("objective",0) if r else 0,",.0f"), r.get("feasible") if r else None, w))
PYEOF
done
