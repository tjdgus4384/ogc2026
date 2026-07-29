#!/usr/bin/env bash
#SBATCH --job-name=build
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=00:30:00
#SBATCH --output=/data_fast/home/tjdgus4384/priv/ogc2026/runs/battery-v5.log
#
# v5temp (acceptance-targeted temperature) battery:
#   (0) build  (1) canaries p1/p2 (report; v2.1 refs 1499/3690)
#   (2) same-seed determinism
#   (3) freeze diagnostic: acc fraction v2.1 vs v5temp, T20 and T120 --
#       the legacy schedule should show acc collapsing with wall time
#       (frozen), the controller should hold it near target.
set -euo pipefail

ROOT=/data_fast/home/tjdgus4384/priv/ogc2026
PY=$ROOT/.miniforge/envs/ogc2026/bin/python

echo "=== (0) build ==="
OGC_SOLVER=$ROOT/exp/v5temp PY=$PY bash $ROOT/exp/v5temp/build.sh

echo "=== (1) canaries (v2.1 refs: p1 1499, p2 3690) ==="
for P in 1 2; do
    OGC_SOLVER=$ROOT/exp/v5temp "$PY" $ROOT/tools/run_instance.py $P 20
done

echo "=== (2) determinism: same seed twice (p27 reconstruct + 5s anneal) ==="
OGC_SOLVER=$ROOT/exp/v5temp "$PY" - <<'PYEOF'
import sys, json, os
sys.path.insert(0, os.environ["OGC_SOLVER"])
import myalgorithm as MA
prob = json.load(open(
    "/data_fast/home/tjdgus4384/priv/ogc2026/data/train/prob_27.json"))
outs = []
for rep in range(2):
    eng, _ = MA._seed_free(prob, diversify=False)
    eng.run_alns(5.0, 0.05, 0.15, 0.002, 0.9995, 7, 0, 1.0, 0.0, 0x1F)
    outs.append(json.dumps(eng.get_solution(), sort_keys=True))
same = outs[0] == outs[1]
print(json.dumps({"determinism_same_seed": same}))
assert same, "same-seed reruns diverged"
PYEOF

echo "=== (3) freeze diagnostic: acc fraction, v2.1 vs v5temp ==="
for W in 20 120; do
    echo "--- T$W v2.1 ---"
    OGC_SOLVER=$ROOT/solver "$PY" $ROOT/tools/probe_vpthreads.py 38 1 $W
    echo "--- T$W v5temp ---"
    OGC_SOLVER=$ROOT/exp/v5temp "$PY" $ROOT/tools/probe_vpthreads.py 38 1 $W
done
echo "=== battery done ==="
