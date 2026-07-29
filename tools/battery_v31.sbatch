#!/usr/bin/env bash
#SBATCH --job-name=build
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=00:20:00
#SBATCH --output=/data_fast/home/tjdgus4384/priv/ogc2026/runs/battery-v31.log
#
# exp/v31det battery after diagnostic-infra fold: canaries MUST stay byte-exact (all additions are gated/inert)
# 
set -euo pipefail

ROOT=/data_fast/home/tjdgus4384/priv/ogc2026
PY=$ROOT/.miniforge/envs/ogc2026/bin/python
export OGC_SOLVER=$ROOT/exp/v31det

echo "=== (0) build ==="
PY=$PY bash $ROOT/exp/v31det/build.sh

echo "=== (1) canaries (MUST equal v2.1: p1 1499, p2 3690) ==="
for P in 1 2; do
    "$PY" $ROOT/tools/run_instance.py $P 20
done
echo "=== battery done ==="

echo "=== (2) legacy mode must be byte-exact v2.1 canaries ==="
for P in 1 2; do
    OGC_CORE=0 OGC_SOLVER=$ROOT/exp/v31det "$PY" $ROOT/tools/run_instance.py $P 20
done
echo "=== (3) governed-mode reproducibility: same seed twice on real P4 T120 ==="
OGC_SOLVER=$ROOT/exp/v31det OGC_DATA=preliminary_test "$PY" - <<'PYEOF'
import sys, json, os
sys.path.insert(0, os.environ["OGC_SOLVER"])
import myalgorithm as MA
prob = json.load(open(
    "/data_fast/home/tjdgus4384/priv/ogc2026/data/preliminary_test/prob_4.json"))
outs = []
for rep in range(2):
    sol = MA.algorithm(prob, 120)
    outs.append(json.dumps(sol, sort_keys=True))
print(json.dumps({"same_seed_same_node_identical": outs[0] == outs[1]}))
PYEOF
echo "=== v31 battery done ==="
