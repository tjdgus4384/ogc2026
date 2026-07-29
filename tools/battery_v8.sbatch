#!/usr/bin/env bash
#SBATCH --job-name=build
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=00:20:00
#SBATCH --output=/data_fast/home/tjdgus4384/priv/ogc2026/runs/battery-v8.log
#
# v7q battery: build + QMUL=1 must stay byte-identical to v2.1
# (0.2*qhiF*n == 0.03n at defaults -- exact canaries required).
set -euo pipefail

ROOT=/data_fast/home/tjdgus4384/priv/ogc2026
PY=$ROOT/.miniforge/envs/ogc2026/bin/python
export OGC_SOLVER=$ROOT/exp/v8win

echo "=== (0) build ==="
PY=$PY bash $ROOT/exp/v8win/build.sh

echo "=== (1) canaries (report; op10 changes trajectories -- v2.1 refs 1499/3690) ==="
for P in 1 2; do
    "$PY" $ROOT/tools/run_instance.py $P 20
done
echo "=== battery done ==="

echo "=== (2) determinism: same seed twice (p27 reconstruct + 5s anneal) ==="
OGC_SOLVER=$ROOT/exp/v8win "$PY" - <<'PYEOF'
import sys, json, os
sys.path.insert(0, os.environ["OGC_SOLVER"])
import myalgorithm as MA
prob = json.load(open(
    "/data_fast/home/tjdgus4384/priv/ogc2026/data/train/prob_27.json"))
outs = []
for rep in range(2):
    eng, _ = MA._seed_free(prob, diversify=False)
    eng.run_alns(5.0, 0.05, 0.15, 0.002, 0.9995, 7, 0, 0.75, 0.06, 0x7FF)
    outs.append(json.dumps(eng.get_solution(), sort_keys=True))
same = outs[0] == outs[1]
print(json.dumps({"determinism_same_seed": same}))
assert same, "same-seed reruns diverged"
PYEOF
echo "=== (3) op10 exercise: p38 T60 explore persona, audit on ==="
OGC_SOLVER=$ROOT/exp/v8win "$PY" - <<'PYEOF'
import sys, json, os
sys.path.insert(0, os.environ["OGC_SOLVER"])
import myalgorithm as MA
prob = json.load(open(
    "/data_fast/home/tjdgus4384/priv/ogc2026/data/train/prob_38.json"))
eng, _ = MA._seed_free(prob, diversify=False)
r = eng.run_alns(60.0, 0.05, 0.15, 0.002, 0.9995, 7, 500, 0.75, 0.06, 0x7FF)
print(json.dumps({"iters": int(r[0]), "acc": round(r[1]/max(1,r[0]),3),
                  "failed": int(r[3]), "best": r[4],
                  "badIter": int(r[5]), "badKind": int(r[6]),
                  "badPhase": int(r[7])}))
assert r[5] < 0, "AUDIT FAILURE: op10 corrupted state"
PYEOF
echo "=== v8 battery done ==="
