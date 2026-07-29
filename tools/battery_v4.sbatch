#!/usr/bin/env bash
#SBATCH --job-name=build
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=00:30:00
#SBATCH --output=/data_fast/home/tjdgus4384/priv/ogc2026/runs/battery-v4.log
#
# v4spec build + validation battery:
#   (0) build            (1) omp_probe: does a num_threads(4) region get 4?
#   (2) canaries p1/p2   (3) determinism: vp1 vs vp4 byte-identical reconstruct
#   (4) throughput: p27/p38 single-arm T60, vp1 vs vp4 iters
set -euo pipefail

ROOT=/data_fast/home/tjdgus4384/priv/ogc2026
PY=$ROOT/.miniforge/envs/ogc2026/bin/python
export OGC_SOLVER=$ROOT/exp/v4spec

echo "=== (0) build ==="
PY=$PY bash $ROOT/exp/v4spec/build.sh

echo "=== (1) omp_probe ==="
"$PY" -c "
import sys, os; sys.path.insert(0, os.environ['OGC_SOLVER'])
import sscore
t = sscore.omp_probe(4)
print({'omp_team': t})
assert t == 4, 'parallel regions collapse to team of %d -- battery would be vacuous' % t
"

echo "=== (2) canaries ==="
for P in 1 2; do
    "$PY" $ROOT/tools/run_instance.py $P 20
done

echo "=== (3) determinism: vp1 vs vp4 on fixed EDD reconstruction (p27) ==="
"$PY" - <<'PYEOF'
import sys, json, os
sys.path.insert(0, os.environ["OGC_SOLVER"])
import myalgorithm as MA
prob = json.load(open(
    "/data_fast/home/tjdgus4384/priv/ogc2026/data/train/prob_27.json"))
n = len(prob["blocks"])
D = [int(round(b["due_date"])) for b in prob["blocks"]]
order = sorted(range(n), key=lambda i: D[i])
sols, rcs = [], []
for vpt in (1, 4):
    eng, _ = MA._seed_free(prob, diversify=False)
    eng.set_vp_threads(vpt)
    rc = eng.reconstruct_ordered([-1] * n, order, False, 7)
    rcs.append(rc)
    sols.append(json.dumps(eng.get_solution(), sort_keys=True))
assert rcs == [-1, -1], f"reconstruction failed: rc={rcs}"
print(json.dumps({"determinism_vp1_eq_vp4": sols[0] == sols[1], "rc": rcs}))
assert sols[0] == sols[1], "vp4 diverged from vp1"
PYEOF

echo "=== (4) throughput: single-arm T60, vp1 vs vp4 ==="
for P in 27 38; do
    for VPT in 1 4; do
        "$PY" $ROOT/tools/probe_vpthreads.py $P $VPT 60
    done
done
echo "=== battery done ==="
