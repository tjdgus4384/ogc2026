#!/bin/bash
#SBATCH -J seedcost
#SBATCH -N 1
#SBATCH -c 8
#SBATCH -o slurm-%j.out
export PYTHONNOUSERSITE=1
PY="$PWD/.miniforge/envs/ogc2026/bin/python"
OMP_NUM_THREADS=4 taskset -c 0-3 $PY -u - <<'PY'
# What does one construction cost, against the official limit? sq1 adds a
# seventh insertion order, so if construction is a large share of the budget
# the order could be right and still lose on time alone.
import json, sys, time, math, pathlib
REPO = pathlib.Path(".").resolve()
sys.path.insert(0, str(REPO / "solver"))
import myalgorithm as ma
TL = {3: 240, 4: 480, 5: 600, 6: 900}
for i in (3, 4, 5, 6):
    p = json.load(open(REPO / "data" / "preliminary_test" / f"prob_{i}.json"))
    B = p["blocks"]; n = len(B)
    eng, D, P = ma._fresh(p)
    order = sorted(range(n), key=lambda k: (D[k], -P[k]))
    t0 = time.time()
    ok = eng.reconstruct_ordered([-1] * n, order, False, 7)
    el = time.time() - t0
    print(f"P{i}: one construction {el:6.2f}s  ({el / TL[i] * 100:4.1f}% of the "
          f"{TL[i]}s limit)   six orders {el*6:6.1f}s  seven {el*7:6.1f}s"
          f"   -> a 7th order costs {el / TL[i] * 100:.1f}% of budget"
          f"  [rc={ok}]")
PY
