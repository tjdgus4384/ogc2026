"""How much of the Z1=0 area plan can the geometry engine actually hold?

Today's CP-SAT probe proved our boards are EXACTLY optimal over the most
congested 30-block bay-window (all bays, orientations, corner positions,
entry days). So the residual gap is not reachable by local rearrangement
-- it lives in the global bay assignment and entry ordering. The area
relaxation says a Z1=0 assignment exists for P4 (z1bound.log: OPTIMAL,
lower bound 0), and runs/plan_p*.json carries it.

This measures the realizable part directly, on the CURRENT engine:
  free    - the engine as shipped (control)
  bay     - the plan's bay assignment locked, entry free
  both    - bay locked and entry capped at the plan's day
  entry   - entry capped only

Locking is not a proposal to ship; it is the measurement that says
whether chasing the plan is worth building. Z1 is 76% of P4's objective,
so even partial realization would dwarf every local lever measured.

Usage: planchase.py <pnum> <mode> <seconds>
"""
import json
import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "solver"))
os.chdir(ROOT)

import sscore

pnum = int(sys.argv[1])
mode = sys.argv[2]
secs = float(sys.argv[3])
seed = int(os.environ.get("OGC_SEED", "7"))

prob = json.load(open(f"data/preliminary_test/prob_{pnum}.json"))
plan = json.load(open(f"runs/plan_p{pnum}.json"))
B = prob["blocks"]
bays = prob["bays"]
W = prob["weights"]
n = len(B)
P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
R = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]
D = [int(round(b["due_date"])) for b in B]
PREF = [[float(v) for v in b["bay_preferences"]] for b in B]

eng = sscore.Engine()
eng.init_problem(n, len(bays), R, D, P,
                 [float(b["workload"]) for b in B], PREF,
                 [float(b["width"]) for b in bays],
                 [float(b["height"]) for b in bays],
                 float(W["w1"]), float(W["w2"]), float(W["w3"]))
for i in range(n):
    for oi in range(len(B[i]["shape"])):
        L = [[list(v) for v in lay] for lay in B[i]["shape"][oi]["layers"]]
        rx, ry = L[0][0]
        eng.add_orient(i, [[(x - rx, y - ry) for (x, y) in lay] for lay in L])
eng.build_occ(max(D) + max(P) + 40)

if mode in ("bay", "both"):
    eng.set_assignment_lock([int(plan[i]["bay"]) for i in range(n)])
if mode in ("entry", "both"):
    # cap = the plan's entry day, but never below the block's release and
    # never above its punctual entry: an unreachable cap would just make
    # every insert fail.
    eng.set_entry_lock([max(R[i], min(int(plan[i]["entry"]), D[i] - P[i]))
                        for i in range(n)])

# build a start the same way the shipped driver does: due-date order,
# assignment free unless this mode locks it. Without a start the
# portfolio runs on an empty board and reports a zero objective.
order = sorted(range(n), key=lambda i: (D[i], -P[i]))
lockv = ([int(plan[i]["bay"]) for i in range(n)]
         if mode in ("bay", "both") else [-1] * n)
rc = eng.reconstruct_ordered(lockv, order, False, seed)
start = eng.get_solution()
print(json.dumps({"reconstruct_rc": rc,
                  "start_placed": sum(start[i*7+6] for i in range(n)),
                  "start_obj": eng.objective()[3]}), flush=True)

t0 = time.time()
eng.run_portfolio(4, secs, 1, 0.05, 0.15, 0.002, 0.9997, seed, 1, [start], 0)
wall = time.time() - t0
z1, z2, z3, tot = eng.objective()
sol = eng.get_solution()
placed = sum(sol[i*7+6] for i in range(n))
on_plan_bay = sum(1 for i in range(n)
                  if sol[i*7+6] and sol[i*7] == int(plan[i]["bay"]))
tardy = sum(1 for i in range(n) if sol[i*7+6] and sol[i*7+5] > D[i])
bad = [i for i in range(n) if sol[i*7+6] and not eng.vp_ok(i)]
print(json.dumps({"p": pnum, "mode": mode, "secs": round(wall, 1),
                  "obj": tot, "z1": z1, "z2": z2, "z3": z3,
                  "placed": placed, "n": n, "tardy_blocks": tardy,
                  "on_plan_bay": on_plan_bay,
                  "vp_violations": len(bad)}), flush=True)
