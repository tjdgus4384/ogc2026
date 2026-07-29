#!/usr/bin/env bash
#SBATCH --job-name=build
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=00:20:00
#SBATCH --output=/data_fast/home/tjdgus4384/priv/ogc2026/runs/battery-v14.log
#
# exp/v14exact battery after diagnostic-infra fold: canaries MUST stay byte-exact (all additions are gated/inert)
# 
set -euo pipefail

ROOT=/data_fast/home/tjdgus4384/priv/ogc2026
PY=$ROOT/.miniforge/envs/ogc2026/bin/python
export OGC_SOLVER=$ROOT/exp/v14exact

echo "=== (0) build ==="
PY=$PY bash $ROOT/exp/v14exact/build.sh

echo "=== (1) canaries (MUST equal v2.1: p1 1499, p2 3690) ==="
for P in 1 2; do
    "$PY" $ROOT/tools/run_instance.py $P 20
done
echo "=== battery done ==="

echo "=== (2) audit + determinism: p38 with window_exact exercised ==="
OGC_SOLVER=$ROOT/exp/v14exact "$PY" - <<'PYEOF'
import sys, json, os, time
sys.path.insert(0, os.environ["OGC_SOLVER"])
import myalgorithm as MA
prob = json.load(open(
    "/data_fast/home/tjdgus4384/priv/ogc2026/data/train/prob_38.json"))
outs = []
for rep in range(2):
    eng, _ = MA._seed_free(prob, diversify=False)
    eng.run_alns(20.0, 0.05, 0.15, 0.002, 0.9995, 7, 0, 0.75, 0.06, 0x3FF,
                 8000)
    ob0 = eng.objective()[3]
    t0 = time.time()
    wins = eng.window_exact(10.0)
    ob1 = eng.objective()[3]
    code = eng.audit_state()
    outs.append(json.dumps(eng.get_solution(), sort_keys=True))
    print(json.dumps({"rep": rep, "obj_before": ob0, "obj_after": ob1,
                      "wins": wins, "wx_s": round(time.time()-t0, 2),
                      "audit": code}))
    assert code == 0, "STATE CORRUPTED by window_exact"
    assert ob1 <= ob0 + 1e-6, "window_exact worsened the objective"
print(json.dumps({"determinism": outs[0] == outs[1]}))
assert outs[0] == outs[1], "window_exact nondeterministic"
PYEOF
echo "=== v14 battery done ==="
