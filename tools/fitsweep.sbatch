#!/usr/bin/env bash
#SBATCH --job-name=probe
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=00:40:00
#SBATCH --output=/data_fast/home/tjdgus4384/priv/ogc2026/runs/fitsweep.log
#
# fitScore component sweep, single-arm T20 anneal on p27/p38 (training) and
# the REAL P4 (T60). v2.1 references: p27 ~29.5M, p38 ~48.2M @T20.
# Variants: (wallw, gamma, blw)
#   A 1,0.1,0    original score (already measured: 32.5M / 56.9M -- worse)
#   B 1,0.1,10   bottom-left dominant, contact+affinity as tiebreak
#   C 1,0,0      pure contact, no exit affinity
#   D 0,0,10     pure BL reproduction (sanity: should match v2.1 refs)
#   E 0.5,0.1,1  balanced blend
set -euo pipefail

ROOT=/data_fast/home/tjdgus4384/priv/ogc2026
PY=$ROOT/.miniforge/envs/ogc2026/bin/python
export OGC_SOLVER=$ROOT/exp/v6fit

PY=$PY bash $ROOT/exp/v6fit/build.sh

run_variant() {
    local name=$1 wallw=$2 gamma=$3 blw=$4
    echo "=== variant $name (wallw=$wallw gamma=$gamma blw=$blw) ==="
    for P in 27 38; do
        OGC_FIT_WALLW=$wallw OGC_FIT_GAMMA=$gamma OGC_FIT_BLW=$blw \
            "$PY" $ROOT/tools/probe_vpthreads.py $P 1 20
    done
    OGC_DATA=preliminary_test OGC_FIT_WALLW=$wallw OGC_FIT_GAMMA=$gamma \
        OGC_FIT_BLW=$blw "$PY" $ROOT/tools/probe_vpthreads.py 4 1 60
}

run_variant A 1 0.1 0
run_variant B 1 0.1 10
run_variant C 1 0 0
run_variant D 0 0 10
run_variant E 0.5 0.1 1
echo "=== fitsweep done ==="
