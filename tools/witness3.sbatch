#!/usr/bin/env bash
#SBATCH -J witness3
#SBATCH -N 1
#SBATCH -p titan
#SBATCH -c 8
#SBATCH --mem=24G
#SBATCH -t 00:40:00
#SBATCH -o runs/logs/witness3_%j.out
#SBATCH -e runs/logs/witness3_%j.out
set -uo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
cd "$REPO"

echo "===== REBUILD builds/wit (adds Ctx::noReserve, the guard-off outcome test, RNG-paired)"
PY="$PY" bash "$REPO/builds/wit/build.sh" 2>&1 | tail -3
ls -l "$REPO"/builds/wit/sscore*.so

echo "===== witness + mechanism + guard-off, all three boards"
echo "===== calibration: seated must still be P4 94, P5 135, P6 205"
for a in "prob_4 480 7" "prob_5 600 7" "prob_6 900 7"; do
  OGC_SEED=7 "$PY" -u "$REPO/tools/witness.py" wit $a || true
  echo
done
echo "===== done"
