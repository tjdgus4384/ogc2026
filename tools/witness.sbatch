#!/usr/bin/env bash
#SBATCH -J witness
#SBATCH -N 1
#SBATCH -p titan
#SBATCH -c 8
#SBATCH --mem=24G
#SBATCH -t 00:40:00
#SBATCH -o runs/logs/witness_%j.out
#SBATCH -e runs/logs/witness_%j.out
set -uo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"
cd "$REPO"

echo "===== PASS 1: builds/ordf (the shipped kernel + entryFloor, untouched)"
echo "===== calibration: must reproduce P4 94/150, P5 135/200, P6 205/250"
for a in "prob_4 480 7" "prob_5 600 7" "prob_6 900 7"; do
  OGC_SEED=7 "$PY" -u "$REPO/tools/witness.py" ordf $a || true
  echo
done

echo "===== BUILD builds/wit (= ordf + a counter vector filled by insertGreedy)"
PY="$PY" bash "$REPO/builds/wit/build.sh" 2>&1 | tail -5
ls -l "$REPO"/builds/wit/sscore*.so

echo "===== PASS 2: builds/wit -- the mechanism, measured instead of inferred"
for a in "prob_4 480 7" "prob_5 600 7" "prob_6 900 7"; do
  OGC_SEED=7 "$PY" -u "$REPO/tools/witness.py" wit $a || true
  echo
done
echo "===== done"
