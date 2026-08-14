#!/usr/bin/env bash
#SBATCH -J icmpv2
#SBATCH -N 1
#SBATCH -c 8
#SBATCH --mem=16G
#SBATCH -t 00:15:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
for pid in prob_4 prob_6; do
  echo "== $pid 90s anneal, kick every 10s =="
  OGC_ICMP=10 SMAX_DEBUG=1 OGC_SEED=7 "$REPO/.miniforge/envs/ogc2026/bin/python" -c "
import json,sys
sys.path.insert(0,'$REPO/builds/icmpv')
import myalgorithm as ma
p=json.load(open('$REPO/data/preliminary_test/$pid.json'))
eng,seeds=ma._seed_free(p)
starts=[seeds[0],[]]+seeds[1:3]
eng.run_portfolio(ma._THR,90.0,1,0.05,0.15,0.002,0.9995,7,1,starts)
print('final obj %.0f'%eng.objective()[3])
" 2>&1 | grep -E '^\[icmp\]|final obj' | sort | uniq -c | sort -k2 | head -50
done
