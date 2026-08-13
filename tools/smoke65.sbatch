#!/usr/bin/env bash
#SBATCH -J smoke65
#SBATCH -N 1
#SBATCH -c 8
#SBATCH --mem=24G
#SBATCH -t 00:40:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
OGC_SEED=7 "$PY" -u - <<'EOF'
import json,sys,time,pathlib
REPO=pathlib.Path("/data_fast/home/tjdgus4384/priv/ogc2026")
sys.path.insert(0,str(REPO/"solver"))
import myalgorithm as ma
for pid,tl in (("prob_1",60),("prob_2",120),("prob_3",240),("prob_4",480)):
    p=json.load(open(REPO/"data"/"preliminary_test"/f"{pid}.json"))
    t0=time.time(); sol=ma.algorithm(p,tl); w=time.time()-t0
    res=ma._check(p,sol)
    n=len(p["blocks"]); placed=len(sol) if isinstance(sol,list) else -1
    print(f"{pid}: wall {w:.1f}s/{tl}  feasible={res.get('feasible') if res else None}"
          f"  obj={res.get('objective') if res else None}  recs={placed}/{n}",flush=True)
EOF
