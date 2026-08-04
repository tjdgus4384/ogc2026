#!/usr/bin/env bash
#SBATCH --job-name=probeS
#SBATCH --nodes=1 --ntasks=1 --cpus-per-task=4 --mem=16G --time=01:00:00
#SBATCH --output=/data_fast/home/tjdgus4384/priv/ogc2026/runs/probe_entry.log
set -euo pipefail
cd /data_fast/home/tjdgus4384/priv/ogc2026
true
.miniforge/envs/ogc2026/bin/python tools/m1_beam.py 40
  data/preliminary_test/prob_4.json runs/shipcheck-v31d12/prob_4.sol.json \
  37 81 58 146 116 120 68 148
