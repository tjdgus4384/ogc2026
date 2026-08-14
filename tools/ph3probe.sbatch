#!/usr/bin/env bash
#SBATCH -J ph3probe
#SBATCH -p titan
#SBATCH -N 1
#SBATCH -c 8
#SBATCH --mem=24G
#SBATCH -t 02:00:00
set -uo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
PY="$REPO/.miniforge/envs/ogc2026/bin/python"

echo "=== BUILD builds/ph3 on $(hostname) ==="
set -e
PY="$PY" bash "$REPO/builds/ph3/build.sh"
set +e
SO="$REPO/builds/ph3/sscore.cpython-312-x86_64-linux-gnu.so"
ls -l "$SO"
test -s "$SO" || { echo "FATAL: .so missing or empty"; exit 1; }
"$PY" -c "import sys; sys.path.insert(0,'$REPO/builds/ph3'); import sscore; print('IMPORT OK', sscore.__file__)" || exit 1

# arm 0 must reproduce the shipped pinned numbers EXACTLY (P4 94, P5 135,
# P6 205). If it does not, every other arm is uninterpretable.
for arm in "0 0" "1 0" "0 1" "1 1"; do
  set -- $arm
  echo "############ ARM PH3SNAP=$1 NOSCREEN=$2 ############"
  for b in "prob_4 480 7" "prob_5 600 7" "prob_6 900 7"; do
    OGC_SEED=7 OGC_PH3SNAP=$1 OGC_NOSCREEN=$2 \
      "$PY" -u "$REPO/tools/ph3probe.py" ph3 $b || echo "  (arm failed on $b)"
  done
done
echo "=== DONE ==="
