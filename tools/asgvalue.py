"""Is the gap in the ASSIGNMENT or in the packing given the assignment?

Every local class is measured dead and every exact re-arrangement of a
FIXED assignment returned 0.0%, so the remaining suspect is the joint
(which blocks share a bay) decision. This measures its value directly,
with the production engine's exact geometry and exact time axis: lock a
whole assignment, let the engine spend its full budget under that lock,
and read the achievable objective.

    incumbent   the assignment the shipped solver converged to
    pref        preference-greedy (maximise Z3 quality)
    bal         workload-balanced (minimise Z2)
    mix         preference minus congestion
    swapK       incumbent with K random cross-bay swaps (locality probe)

If the incumbent's lock is the best of these, the assignment is not the
binding constraint and the gap lives inside a bay. If some alternative
is far better, the search is stuck in the wrong basin and the master
decision is where a stronger method must act.

  asgvalue.py <prob.json> <out.json> <seconds> <kind> [board.sol.json]
"""
import json
import math
import os
import random
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "solver"))
os.chdir(ROOT)
import myalgorithm as M  # noqa: E402

prob_path, out_path = sys.argv[1], sys.argv[2]
SEC = float(sys.argv[3]) if len(sys.argv) > 3 else 240.0
KIND = sys.argv[4] if len(sys.argv) > 4 else "incumbent"
BOARD = sys.argv[5] if len(sys.argv) > 5 else None

p = json.load(open(prob_path))
B, bays, W = p["blocks"], p["bays"], p["weights"]
n, m = len(B), len(bays)
L = [float(b["workload"]) for b in B]
PREF = [[float(v) for v in b["bay_preferences"]] for b in B]
ar = [b["width"] * b["height"] for b in bays]
u = [(sum(ar) / m) / a for a in ar]
rng = random.Random(20260804)

inc = None
if BOARD:
    inc = [-1] * n
    for t, day in json.load(open(BOARD))["operations"].items():
        for o in day:
            if o["type"] == "ENTRY":
                inc[o["block_id"]] = o["bay_id"]


def greedy(score):
    load = [0.0] * m
    asg = [0] * n
    for i in sorted(range(n), key=lambda q: -L[q]):
        j = max(range(m), key=lambda q: score(i, q, load))
        asg[i] = j
        load[j] += L[i]
    return asg


if KIND == "incumbent":
    asg = inc
elif KIND == "pref":
    asg = greedy(lambda i, j, ld: PREF[i][j])
elif KIND == "bal":
    asg = greedy(lambda i, j, ld: -u[j] * ld[j])
elif KIND == "mix":
    asg = greedy(lambda i, j, ld: PREF[i][j] - 0.02 * u[j] * ld[j])
elif KIND.startswith("file:"):
    # geometry-free optimum (tools/asgopt.py). Z2 and Z3 depend only on
    # the assignment, so this is the best those two terms can be; the
    # question this probe answers is what the engine's exact geometry
    # then charges in tardiness for insisting on it.
    asg = json.load(open(KIND[5:]))
elif KIND.startswith("swap"):
    k = int(KIND[4:])
    asg = list(inc)
    for _ in range(k):
        a, b = rng.randrange(n), rng.randrange(n)
        asg[a], asg[b] = asg[b], asg[a]
else:
    sys.exit(f"unknown kind {KIND}")

HARD = os.environ.get("ASG_HARD", "1") == "1"
t0 = time.time()
eng, seeds = M._seed_free(p, asg=asg)
res = {"board": os.path.basename(prob_path), "kind": KIND, "n": n, "m": m}
if eng is None:
    res["error"] = "seed failed under this lock"
    print(json.dumps(res)); raise SystemExit
hor = max(int(round(b["due_date"])) for b in B) \
    + max(int(math.ceil(b["processing_time"] - 1e-9)) for b in B) + 40
# _seed_free only locks the assignment during CONSTRUCTION; the anneal
# then runs free and drifts off it. Measuring the construction point
# separately is the only way to tell "geometry rejected this assignment"
# apart from "the search walked away from it".
z0 = eng.objective()
res_pre = {"constructed_obj": z0[3], "z1": z0[0], "z2": z0[1], "z3": z0[2]}
if HARD and asg is not None:
    eng.set_assignment_lock(list(asg))      # persistent: threads inherit it
eng.run_portfolio(4, SEC, 1, 0.05, 0.15, 0.002, 0.9995, 7, len(seeds), seeds)
z = eng.objective()
res.update({"pre": res_pre, "hard_lock": HARD,
            "wall": round(time.time() - t0, 1), "objective": z[3],
            "z1": z[0], "z2": z[1], "z3": z[2],
            "placed": sum(1 for i in range(n) if eng.get_solution()[i*7+6])})
sol = M._recs_to_sol(M._extract(eng, n))
chk = M._check(p, sol)
res["checker_feasible"] = None if chk is None else bool(chk.get("feasible"))
res["checker_obj"] = None if chk is None else chk.get("objective")
if chk is not None and chk.get("feasible") and out_path != "/dev/null":
    json.dump(sol, open(out_path, "w"))
print(json.dumps(res))
