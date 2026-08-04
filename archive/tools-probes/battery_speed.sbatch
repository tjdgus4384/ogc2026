#!/usr/bin/env bash
#SBATCH --job-name=build
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=00:30:00
#SBATCH --output=/data_fast/home/tjdgus4384/priv/ogc2026/runs/battery-speed.log
#
# Speed-optimization contract battery: v40speed must be BYTE-IDENTICAL to
# solver v3.0 on fixed-iteration trajectories (same decisions, faster),
# then measure iters at fixed wall.
set -euo pipefail
ROOT=/data_fast/home/tjdgus4384/priv/ogc2026
PY=$ROOT/.miniforge/envs/ogc2026/bin/python

echo "=== build v40speed ==="
OGC_SOLVER=$ROOT/exp/v40speed PY=$PY bash $ROOT/exp/v40speed/build.sh

echo "=== (1) byte-identity: fixed 8000 iters, p27 + real P4, ref vs v40 ==="
"$PY" - <<'PYEOF'
import sys, json, importlib, os
def run(dirp, data, pid, iters):
    sys.path.insert(0, dirp)
    for m in ("myalgorithm", "sscore", "utils"):
        if m in sys.modules: del sys.modules[m]
    import myalgorithm as MA
    prob = json.load(open(
        f"/data_fast/home/tjdgus4384/priv/ogc2026/data/{data}/prob_{pid}.json"))
    eng, _ = MA._seed_free(prob, diversify=False)
    r = eng.run_alns(600.0, 0.05, 0.15, 0.002, 0.9995, 7, 0, 0.75, 0.06,
                     0x3FF, iters)
    out = (int(r[0]), json.dumps(eng.get_solution(), sort_keys=True))
    sys.path.pop(0)
    return out
ROOT = "/data_fast/home/tjdgus4384/priv/ogc2026"
for data, pid, iters in [("train", 27, 8000), ("preliminary_test", 4, 20000)]:
    a = run(f"{ROOT}/solver", data, pid, iters)
    b = run(f"{ROOT}/exp/v40speed", data, pid, iters)
    same = a == b
    print(json.dumps({"case": f"{data}/p{pid}", "iters": [a[0], b[0]],
                      "byte_identical": same}))
    assert same, "TRAJECTORY CHANGED -- optimization is not semantics-preserving"
PYEOF

echo "=== (2) speed: T20 fixed-wall iters, ref vs v40 (p27 / p38 / real P4) ==="
for CFG in "train 27" "train 38" "preliminary_test 4"; do
  set -- $CFG
  for D in solver exp/v40speed; do
    OGC_SOLVER=$ROOT/$D OGC_DATA=$1 "$PY" $ROOT/tools/probe_vpthreads.py $2 1 20
  done
done
echo "=== speed battery done ==="
