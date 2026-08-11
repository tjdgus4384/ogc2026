#!/usr/bin/env bash
#SBATCH -J assign
#SBATCH -N 1
#SBATCH -c 4
#SBATCH --mem=32G
#SBATCH -t 00:40:00
#
#   sbatch -p titan tools/assign.sbatch prob_4 runs/base53-s41/prob_4.sol.json
#
# Same board, same question, three feasibility tests: how many
# preference-improving punctual bay relocations does each kernel admit?
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
PID="$1"; SOL="$2"
for d in builds/b53 builds/m7; do
  OMP_NUM_THREADS=4 "$PY" "$REPO/tools/assign_probe.py" "$d" "$PID" "$REPO/$SOL" || true
done
