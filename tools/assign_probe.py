"""Assignment-descent probe: measure _assign_descent on a frozen board.

Loads a saved board, replays it into the engine, and runs the driver's
own _assign_descent with a wall budget. Reports objective and the
(Z1, Z2, Z3) split before/after -- the direct test of whether the
exact-delta (relocate + swap) descent harvests the balance/regret share
that the preference-only polish could not see.

Usage: assign_probe.py <prob.json> <sol.json> [seconds]
"""
import json
import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "solver"))
os.chdir(ROOT)
import myalgorithm as M  # noqa: E402

prob_path, sol_path = sys.argv[1], sys.argv[2]
SEC = float(sys.argv[3]) if len(sys.argv) > 3 else 60.0

p = json.load(open(prob_path))
ops = json.load(open(sol_path))["operations"]
B = p["blocks"]
n = len(B)
D = [int(round(b["due_date"])) for b in B]
P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]

flat = [0] * (n * 7)
for t, day in ops.items():
    for op in day:
        i = op["block_id"]
        if op["type"] == "ENTRY":
            flat[i*7:i*7+5] = [op["bay_id"], op["orient_idx"], op["x"],
                               op["y"], int(t)]
            flat[i*7+6] = 1
        else:
            flat[i*7+5] = int(t)

eng = M._build_engine(p)
eng.build_occ(max(D) + max(P) + 40)
eng.set_solution(flat)
eng.build_occ(max(D) + max(P) + 40)
o0 = eng.objective()
t0 = time.time()
M._assign_descent(p, eng, time.time() + SEC)
o1 = eng.objective()
sol = M._recs_to_sol(M._extract(eng, n))
res = M._check(p, sol)
print(json.dumps({
    "board": os.path.basename(sol_path),
    "before": {"obj": o0[3], "z1": o0[0], "z2": o0[1], "z3": o0[2]},
    "after": {"obj": o1[3], "z1": o1[0], "z2": o1[1], "z3": o1[2]},
    "gain_pct": round(100 * (o0[3] - o1[3]) / o0[3], 3),
    "checker_feasible": None if res is None else bool(res.get("feasible")),
    "checker_obj": None if res is None else res.get("objective"),
    "wall": round(time.time() - t0, 1)}))
