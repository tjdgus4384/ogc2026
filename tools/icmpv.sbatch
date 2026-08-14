#!/usr/bin/env bash
#SBATCH -J icmpv
#SBATCH -N 1
#SBATCH -c 8
#SBATCH --mem=16G
#SBATCH -t 00:10:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
OGC_ICMP=5 SMAX_DEBUG=1 OGC_SEED=7 "$REPO/.miniforge/envs/ogc2026/bin/python" -c "
import json,sys
sys.path.insert(0,'$REPO/builds/icmpv')
import myalgorithm as ma
p=json.load(open('$REPO/data/preliminary_test/prob_4.json'))
eng,seeds=ma._seed_free(p)
starts=[seeds[0],[]]+seeds[1:3]
eng.run_portfolio(ma._THR,30.0,1,0.05,0.15,0.002,0.9995,7,1,starts)
print('obj',eng.objective()[3])
" 2>&1 | { grep -c '^\[icmp\]' && echo MARKER_LINES_ABOVE; } ; true
