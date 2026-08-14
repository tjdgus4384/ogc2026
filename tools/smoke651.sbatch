#!/usr/bin/env bash
#SBATCH -J smoke651
#SBATCH -N 1
#SBATCH -c 8
#SBATCH --mem=24G
#SBATCH -t 00:40:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
OGC_SEED=7 "$REPO/.miniforge/envs/ogc2026/bin/python" -u -c "
import json,sys,time
sys.path.insert(0,'$REPO/solver')
import myalgorithm as ma
for pid,tl in (('prob_1',60),('prob_2',120),('prob_3',240),('prob_4',480)):
    p=json.load(open('$REPO/data/preliminary_test/%s.json'%pid))
    t0=time.time(); sol=ma.algorithm(p,float(tl)); w=time.time()-t0
    r=ma._check(p,sol)
    print('%s: wall %.1f/%d feasible=%s obj=%s'%(pid,w,tl,
          r.get('feasible') if r else None, r.get('objective') if r else None),
          flush=True)
"
