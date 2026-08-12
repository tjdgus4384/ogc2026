#!/bin/bash
#SBATCH -J adoptv
#SBATCH -N 1
#SBATCH --exclusive
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
mkdir -p runs/adopt
# 1. the ADOPTED solver must pass all three staged failure paths
i=0
for p in prob_3 prob_4 prob_5 prob_6; do
  c=$((i*8)); OMP_NUM_THREADS=4 taskset -c $c-$((c+7)) \
    $PY -u tools/floorcheck.py solver $p 60 > runs/adopt/fc-$p.txt 2>&1 &
  i=$((i+1))
done
wait
# 2. the SHIPPED ZIP, extracted, must reproduce the known optima
rm -rf runs/adopt/zip && mkdir -p runs/adopt/zip
$PY -c "import zipfile;zipfile.ZipFile('submissions/[OGC2026_AlgorithmCode]_MSGA.zip').extractall('runs/adopt/zip')"
cp solver/utils.py runs/adopt/zip/ 2>/dev/null
for p in prob_1 prob_2; do
  OMP_NUM_THREADS=4 taskset -c 0-7 $PY -u - "$p" <<'PYEOF'
import json,os,sys,time
sys.path.insert(0,'runs/adopt/zip'); os.environ["OGC_SEED"]="7"
import myalgorithm as ma
prob=json.load(open("data/preliminary_test/%s.json"%sys.argv[1]))
t=time.time(); sol=ma.algorithm(prob,timelimit=60); w=time.time()-t
r=ma._check(prob,sol)
print("   ZIP %s  objective %12s  feasible=%s  %.1fs"%(sys.argv[1],
      format(r.get("objective",0) if r else 0,",.0f"), r.get("feasible") if r else None, w))
PYEOF
done
echo "===== staged-failure verification of the ADOPTED solver"
for p in prob_3 prob_4 prob_5 prob_6; do cat runs/adopt/fc-$p.txt; done
