"""Global temporal re-solve (GTR): freeze WHERE, optimize WHEN, exactly.

Every gap we have is Z1, and Z1 is the one dimension the search never
optimizes globally: repair always takes the earliest legal day, and
entry times move only through local removal/reinsert. With the board's
geometry (bay, x, y, ori) FROZEN, feasibility collapses to pairwise
predicates that do not depend on time at all:

  rest(i,k)   - same-layer polygon overlap at the fixed positions
                -> stays must be disjoint
  blk(i,k)    - k's resting geometry intersects i's vertical descent
                shaft (i's layer l vs k's layers j >= l)
                -> i can neither enter nor exit while k is present

That is a disjunctive scheduling problem over n entry times -- job-shop
shaped, which CP-SAT solves globally. n=150-300 with a few thousand
disjunctions is far inside its comfort zone. The answer is re-verified
with the engine's vp oracle; violated pairs get exact cuts and it
re-solves (the predicates are conservative approximations of the five
checker rules, the oracle is the authority).

If tardiness drops materially, this becomes a new global pass; if it is
0, boards are already time-optimal given their geometry and the gap is
purely geometric -- either way the axis is settled by measurement.

Usage: timeopt.py <board.sol.json> <prob.json> [solve_secs]
"""
import json
import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "solver"))
os.chdir(ROOT)

from ortools.sat.python import cp_model
from shapely import affinity
from shapely.geometry import Polygon

import sscore

solf = sys.argv[1]
probf = sys.argv[2]
STL = float(sys.argv[3]) if len(sys.argv) > 3 else 120.0

prob = json.load(open(probf))
board = json.load(open(solf))
B = prob["blocks"]; bays = prob["bays"]; W = prob["weights"]
n = len(B)
P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
R = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]
D = [int(round(b["due_date"])) for b in B]
PREF = [[float(v) for v in b["bay_preferences"]] for b in B]
HOR = (max(D) + max(P)) * 2 + 200

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
eng.build_occ(HOR)

cur = [0] * (n * 7)
for t, ops in board["operations"].items():
    for op in ops:
        if op["type"] == "ENTRY":
            i = op["block_id"]; e = int(t)
            cur[i*7:i*7+7] = [op["bay_id"], op["orient_idx"], op["x"],
                              op["y"], e, e + P[i], 1]
eng.set_solution(cur)
eng.build_occ(HOR)
z1a, z2a, z3a, base = eng.objective()
print(json.dumps({"board": os.path.basename(solf), "incumbent": base,
                  "z1": z1a, "z2": z2a, "z3": z3a}), flush=True)

SHIFT = [[tuple(B[i]["shape"][oi]["layers"][0][0])
          for oi in range(len(B[i]["shape"]))] for i in range(n)]
_poly = {}


def layers(i):
    oi = cur[i*7+1]
    key = (i, oi)
    if key not in _poly:
        rx, ry = SHIFT[i][oi]
        _poly[key] = [affinity.translate(
            Polygon([(px - rx, py - ry) for (px, py) in lay]),
            cur[i*7+2], cur[i*7+3])
            for lay in B[i]["shape"][oi]["layers"]]
    return _poly[key]


t0 = time.time()
rest, blk = set(), set()
bybay = {}
for i in range(n):
    if cur[i*7+6]:
        bybay.setdefault(cur[i*7], []).append(i)
for j, ids in bybay.items():
    for a in range(len(ids)):
        for b in range(a + 1, len(ids)):
            i, k = ids[a], ids[b]
            Li, Lk = layers(i), layers(k)
            if any(Li[l].intersection(Lk[l]).area > 1e-9
                   for l in range(min(len(Li), len(Lk)))):
                rest.add((i, k))
                continue
            # descent shaft of i vs k's upper layers (and vice versa)
            if any(Li[l].intersection(Lk[j2]).area > 1e-9
                   for l in range(len(Li)) for j2 in range(l, len(Lk))):
                blk.add((i, k))          # k blocks i's shaft
            if any(Lk[l].intersection(Li[j2]).area > 1e-9
                   for l in range(len(Lk)) for j2 in range(l, len(Li))):
                blk.add((k, i))          # i blocks k's shaft
print(json.dumps({"pred_secs": round(time.time() - t0, 1),
                  "rest_pairs": len(rest), "blk_pairs": len(blk)}),
      flush=True)


def solve(extra_cuts):
    mdl = cp_model.CpModel()
    E = {}
    for i in range(n):
        if cur[i*7+6]:
            E[i] = mdl.NewIntVar(R[i], HOR - P[i] - 1, f"e{i}")
    for (i, k) in rest:                        # stays disjoint
        b1 = mdl.NewBoolVar("")
        mdl.Add(E[i] + P[i] <= E[k]).OnlyEnforceIf(b1)
        mdl.Add(E[k] + P[k] <= E[i]).OnlyEnforceIf(b1.Not())
    for (i, k) in blk:
        # while k is present, i can neither enter nor exit.
        # entry: e_i <= e_k - 1  OR  e_i >= f_k   (conservative bounds)
        # exit:  f_i <= e_k      OR  f_i >= f_k + 1
        b1 = mdl.NewBoolVar(""); b2 = mdl.NewBoolVar("")
        mdl.Add(E[i] <= E[k] - 1).OnlyEnforceIf(b1)
        mdl.Add(E[i] >= E[k] + P[k]).OnlyEnforceIf(b1.Not())
        mdl.Add(E[i] + P[i] <= E[k]).OnlyEnforceIf(b2)
        mdl.Add(E[i] + P[i] >= E[k] + P[k] + 1).OnlyEnforceIf(b2.Not())
    for (i, k, ei, ek) in extra_cuts:          # no-good on exact days
        bb = mdl.NewBoolVar("")
        mdl.Add(E[i] != ei).OnlyEnforceIf(bb)
        mdl.Add(E[k] != ek).OnlyEnforceIf(bb.Not())
    T = []
    for i in E:
        t = mdl.NewIntVar(0, HOR, f"t{i}")
        mdl.Add(t >= E[i] + P[i] - D[i])
        T.append(t)
    mdl.Minimize(sum(T))
    sv = cp_model.CpSolver()
    sv.parameters.max_time_in_seconds = STL
    sv.parameters.num_search_workers = 4
    st = sv.Solve(mdl)
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, sv.StatusName(st)
    return {i: sv.Value(E[i]) for i in E}, sv.StatusName(st)


cuts = []
for rounds in range(6):
    t0 = time.time()
    times, st = solve(cuts)
    if times is None:
        print(json.dumps({"round": rounds, "status": st})); break
    new = list(cur)
    for i, e in times.items():
        new[i*7+4] = e; new[i*7+5] = e + P[i]
    eng.set_solution(new)
    eng.build_occ(HOR)
    bad = [i for i in range(n) if new[i*7+6] and not eng.vp_ok(i)]
    z1b, z2b, z3b, tot = eng.objective()
    print(json.dumps({"round": rounds, "status": st,
                      "solve_secs": round(time.time() - t0, 1),
                      "z1": z1b, "obj": tot,
                      "delta_pct": round(100*(tot-base)/base, 2),
                      "vp_violations": len(bad)}), flush=True)
    if not bad:
        break
    # cut the exact conflicting day-pairs among violated blocks' bays
    vb = set(bad)
    added = 0
    for i in bad:
        j = new[i*7]
        for k in bybay.get(j, []):
            if k == i: continue
            cuts.append((min(i, k), max(i, k),
                         times[min(i, k)], times[max(i, k)]))
            added += 1
    if not added:
        break
print(json.dumps({"final_cuts": len(cuts)}), flush=True)
