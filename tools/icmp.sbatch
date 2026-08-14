#!/usr/bin/env bash
#SBATCH -J icmp
#SBATCH -N 1
#SBATCH -c 16
#SBATCH --mem=32G
#SBATCH -t 02:30:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
# smoke + first paired look on the finals class: P6 official TL, same seed,
# arms differing only in the env knob. 3 replicates x {off, 10s, 30s}.
for rep in 0 1 2; do
  for k in 0 10 30; do
    OGC_ICMP=$k OGC_SEED=$((7+4*rep)) "$PY" -c "
import json,sys,time
sys.path.insert(0,'$REPO/builds/icmp')
import myalgorithm as ma
p=json.load(open('$REPO/data/preliminary_test/prob_6.json'))
t0=time.time(); sol=ma.algorithm(p,900.0); w=time.time()-t0
r=ma._check(p,sol)
print(f'rep$rep icmp=$k obj=%s feas=%s wall=%.1f'%(r.get('objective') if r else None, r.get('feasible') if r else None, w), flush=True)
"
  done
done
