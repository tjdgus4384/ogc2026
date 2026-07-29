#!/usr/bin/env bash
#SBATCH --job-name=build
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=00:30:00
#SBATCH --output=/data_fast/home/tjdgus4384/priv/ogc2026/runs/battery-v6.log
#
# v6fit (best-fit scored placement) battery:
#   (0) build  (1) canaries p1/p2 (REPORT objectives -- trajectory changed,
#   1499/3690 are the v2.1 references, not assertions)
#   (2) determinism: same seed twice -> byte-identical solution
#   (3) throughput: p27/p38 single-arm T20 iters (fitScore overhead check;
#   v2.1 references ~19k/4.1k at T20)
set -euo pipefail

ROOT=/data_fast/home/tjdgus4384/priv/ogc2026
PY=$ROOT/.miniforge/envs/ogc2026/bin/python
export OGC_SOLVER=$ROOT/exp/v6fit

echo "=== (0) build ==="
PY=$PY bash $ROOT/exp/v6fit/build.sh

echo "=== (1) canaries (v2.1 refs: p1 1499, p2 3690) ==="
for P in 1 2; do
    "$PY" $ROOT/tools/run_instance.py $P 20
done

echo "=== (2) determinism: same seed twice (p27 reconstruct + 5s anneal) ==="
"$PY" - <<'PYEOF'
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

echo "=== (3) throughput p27/p38 T20 (v2.1 refs ~19k / ~4.1k iters) ==="
export SMAX_DEBUG=1
for P in 27 38; do
    "$PY" $ROOT/tools/probe_vpthreads.py $P 1 20
done
echo "=== battery done ==="
