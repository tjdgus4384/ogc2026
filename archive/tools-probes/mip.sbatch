#!/usr/bin/env bash
#SBATCH --job-name=mip
#SBATCH --nodes=1 --ntasks=1 --cpus-per-task=16 --mem=48G --time=00:40:00
#SBATCH --output=/data_fast/home/tjdgus4384/priv/ogc2026/runs/mip_recomb.log
set -euo pipefail
cd /data_fast/home/tjdgus4384/priv/ogc2026
.miniforge/envs/ogc2026/bin/python tools/mip_recomb.py 4 1200
