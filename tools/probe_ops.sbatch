#!/usr/bin/env bash
#SBATCH --job-name=probe
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=00:20:00
#SBATCH --output=/data_fast/home/tjdgus4384/priv/ogc2026/runs/probe-ops.log
#
# Per-op economics on the REAL boards: build v8win (with the [ops] dump),
# then single-arm explore anneals on P3/P4/P6 -- which ops fire, which earn.
set -euo pipefail

ROOT=/data_fast/home/tjdgus4384/priv/ogc2026
PY=$ROOT/.miniforge/envs/ogc2026/bin/python
export OGC_SOLVER=$ROOT/exp/v8win
export SMAX_DEBUG=1
export OGC_DATA=preliminary_test

PY=$PY bash $ROOT/exp/v8win/build.sh

for P in 3 4 6; do
    echo "=== real P$P explore T60 ==="
    "$PY" - $P <<'PYEOF'
import sys, json, os
sys.path.insert(0, os.environ["OGC_SOLVER"])
import myalgorithm as MA
pid = sys.argv[1]
prob = json.load(open(
    f"/data_fast/home/tjdgus4384/priv/ogc2026/data/preliminary_test/prob_{pid}.json"))
eng, _ = MA._seed_free(prob, diversify=False)
r = eng.run_alns(60.0, 0.05, 0.15, 0.002, 0.9995, 7, 0, 0.75, 0.06, 0x7FF)
print(json.dumps({"inst": pid, "iters": int(r[0]),
                  "acc": round(r[1]/max(1,r[0]),3), "failed": int(r[3]),
                  "best": r[4]}))
PYEOF
done
echo "=== probe-ops done ==="
