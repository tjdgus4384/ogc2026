#!/usr/bin/env bash
#SBATCH -J fragcorr
#SBATCH -N 1
#SBATCH -c 32
#SBATCH --mem=48G
#SBATCH -t 03:00:00
#
# Is fragmentation actually CAUSAL for Z1, or just correlated with being a
# congested board? bf1 raised fragmentation 5.7% on P5 and the objective got
# BETTER; on P4 both got worse. Two points cannot tell. This runs the SHIPPED
# solver across many seeds and asks whether a draw with more fragmented tardy
# days also has more Z1 -- testing the DIAGNOSIS, not any candidate.
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
run() { OGC_SEED=$2 "$PY" "$REPO/tools/blocked.py" "$REPO/solver" "$1" "$3" "$2" 2>&1 | sed "s/^/[$1 s$2] /"; }
export -f run; export REPO PY
for s in 7 11 13 17 19 23 29 31; do run prob_5 $s 600 & done; wait
for s in 7 11 13 17 19 23 29 31; do run prob_4 $s 480 & done; wait
