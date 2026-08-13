#!/usr/bin/env bash
#SBATCH -J habitat
#SBATCH -N 1
#SBATCH -c 8
#SBATCH --mem=24G
#SBATCH -t 01:30:00
#SBATCH --array=0-5
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
IDS=(36 13 25 39 18 2 5 26 40 37 15 14)
A=${IDS[$((SLURM_ARRAY_TASK_ID*2))]}
Bq=${IDS[$((SLURM_ARRAY_TASK_ID*2+1))]}
for id in $A $Bq; do
  OGC_DIAG=1 OGC_SEED=7 "$REPO/.miniforge/envs/ogc2026/bin/python" -c "
import json,sys,time
sys.path.insert(0,'$REPO/solver')
import myalgorithm as ma
p=json.load(open('$REPO/data/train_final/prob_$id.json'))
t0=time.time(); sol=ma.algorithm(p,600.0); w=time.time()-t0
r=ma._check(p,sol)
print('RESULT prob_$id obj=%s feas=%s wall=%.1f'%(r.get('objective') if r else None,
      r.get('feasible') if r else None, w), flush=True)
" 2>&1 | grep -E 'RESULT|"diag": "slice"' | sed "s/^/[prob_$id] /"
done
