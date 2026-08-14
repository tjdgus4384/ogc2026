#!/usr/bin/env bash
#SBATCH -J seedlev
#SBATCH -N 1
#SBATCH -c 4
#SBATCH --mem=16G
#SBATCH -t 00:20:00
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
OGC_SEED=7 "$REPO/.miniforge/envs/ogc2026/bin/python" -u - <<'EOF'
"""Anneal leverage per board: seed objective vs the full official run.

The finals-like giants (p36 84M, p25 77M, p13 75M, p2 63M) have dead or
anneal-dominated tails. If the anneal barely improves the seed there, the
lever for that class is CONSTRUCTION, not search dynamics."""
import json, sys, pathlib
REPO = pathlib.Path("/data_fast/home/tjdgus4384/priv/ogc2026")
sys.path.insert(0, str(REPO/"solver"))
import myalgorithm as ma
FINAL = {36:84251378,13:74996523,25:76867009,39:50716511,18:10221335,2:63460469,
         5:7965479,26:26057695,40:9046261,37:17452986,15:13307737,14:692574}
print(f"{'board':<8}{'seed obj':>16}{'final obj':>16}{'anneal gain':>12}")
for i in sorted(FINAL):
    p = json.load(open(REPO/"data"/"train_final"/f"prob_{i}.json"))
    eng, seeds = ma._seed_free(p)
    if eng is None:
        print(f"p{i:<7}  seed failed"); continue
    so = eng.objective()[3]
    fo = FINAL[i]
    print(f"p{i:<7}{so:>16,.0f}{fo:>16,.0f}{(fo-so)/so*100:>11.1f}%", flush=True)
EOF
