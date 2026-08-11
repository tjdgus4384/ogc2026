#!/usr/bin/env bash
#SBATCH -J rigid
#SBATCH -N 1
#SBATCH -c 4
#SBATCH --mem=32G
#SBATCH -t 00:30:00
#
#   sbatch -p titan tools/rigidity.sbatch prob_6 runs/base53-s41/prob_6.sol.json
#
# Runs tools/rigidity_probe.py over the kernels of interest on the SAME board,
# so the only thing that differs is the feasibility test. The 07-30 probe that
# closed the earliest-entry axis ("0 of 226 tardy blocks had an earlier entry")
# asked vpConservative; this asks the corrected kernels the same question.
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
PID="$1"; SOL="$2"
for d in builds/b53 builds/m4 builds/m5; do
  OMP_NUM_THREADS=4 "$PY" "$REPO/tools/rigidity_probe.py" "$d" "$PID" "$REPO/$SOL" || true
done
