#!/usr/bin/env bash
#SBATCH -J prof
#SBATCH -N 1
#SBATCH -c 8
#SBATCH --mem=24G
#SBATCH -t 00:25:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
command -v perf >/dev/null || { echo "NO PERF ON NODE"; exit 0; }
for pid in prob_6 prob_4; do
  echo "=== $pid 120s anneal, perf sampled ==="
  OGC_SEED=7 perf record -F 499 -g --call-graph fp -o /tmp/perf_$pid.data -- "$PY" -c "
import json,sys
sys.path.insert(0,'$REPO/builds/prof')
import myalgorithm as ma
p=json.load(open('$REPO/data/preliminary_test/$pid.json'))
eng,seeds=ma._seed_free(p)
starts=[seeds[0],[]]+seeds[1:3]
eng.run_portfolio(ma._THR,120.0,1,0.05,0.15,0.002,0.9995,7,1,starts)
print('obj %.0f'%eng.objective()[3])
" 2>/dev/null
  perf report -i /tmp/perf_$pid.data --stdio --no-children 2>/dev/null | grep -v '^#' | grep -v '^\s*$' | head -25
  echo "--- callers of the top symbol ---"
  perf report -i /tmp/perf_$pid.data --stdio 2>/dev/null | grep -v '^#' | head -35
  rm -f /tmp/perf_$pid.data
done
