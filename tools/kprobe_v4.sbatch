#!/usr/bin/env bash
#SBATCH --job-name=probe
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=00:15:00
#SBATCH --output=/data_fast/home/tjdgus4384/priv/ogc2026/runs/kprobe-v4.log
#
# Throughput of the speculative phase-2 with geometric block growth
# (K: vpThreads -> x2 per failed block, cap SPEC_MAX) + phase-1 1-task bypass.
set -euo pipefail

ROOT=/data_fast/home/tjdgus4384/priv/ogc2026
PY=$ROOT/.miniforge/envs/ogc2026/bin/python
export OGC_SOLVER=$ROOT/exp/v4spec
export SMAX_DEBUG=1

PY=$PY bash $ROOT/exp/v4spec/build.sh

for P in 27 38; do
    for VPT in 1 4; do
        echo "=== p$P vp$VPT ==="
        "$PY" $ROOT/tools/probe_vpthreads.py $P $VPT 20
    done
done
echo "=== kprobe done ==="
