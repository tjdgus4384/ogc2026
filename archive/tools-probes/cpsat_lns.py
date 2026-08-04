"""Exact CP-SAT sub-solve over a large neighbourhood -- viability probe.

Every local lever measured so far moves the score by fractions of a
percent while the gap to the top ranks is 19-61%. The one genuinely
different method still untried is the standard strong one for this
class: freeze most of the board, free a set of blocks around a
congested window, and solve THAT subproblem exactly, then repeat.
ortools 9.15.6755 is present on the grading server (and here), so this
is allowed; it has simply never been used.

Candidate placements come from the engine's own feasible_positions, so
each one is vp-exact against the FIXED part of the board. Conflicts
between two candidates are screened pairwise here (rest collision plus
the vertical-descent crane sweep in both directions); the screen may be
optimistic, so whatever CP-SAT returns is re-verified with the engine's
oracle and, on failure, cut away and re-solved. Correctness therefore
does not depend on the screen being exact.

What this probe answers, before anything is built into the solver:
  1. how many candidates a freed block actually has
  2. whether CP-SAT closes a K-block subproblem in seconds, not minutes
  3. whether the exact answer beats the incumbent at all

Usage: cpsat_lns.py <pnum> [K] [seconds]
"""
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "solver"))

from ortools.sat.python import cp_model
from shapely.geometry import Point, Polygon
from shapely.prepared import prep
from shapely.ops import unary_union

import sscore

pnum = int(sys.argv[1])
K = int(sys.argv[2]) if len(sys.argv) > 2 else 15
NCAND = int(sys.argv[4]) if len(sys.argv) > 4 else 120
WIN = int(sys.argv[5]) if len(sys.argv) > 5 else 12
TL = float(sys.argv[3]) if len(sys.argv) > 3 else 60.0
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

prob = json.load(open(f"data/preliminary_test/prob_{pnum}.json"))
board = json.load(open(f"runs/mipboards/prob_{pnum}.sol.json"))
B = prob["blocks"]
bays = prob["bays"]
W = prob["weights"]
n = len(B)
P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
R = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]
D = [int(round(b["due_date"])) for b in B]
PREF = [[float(v) for v in b["bay_preferences"]] for b in B]
SMAX = [max(pr) for pr in PREF]


def build_engine():
    eng = sscore.Engine()
    eng.init_problem(
        n, len(bays), R, D, P,
        [float(b["workload"]) for b in B], PREF,
        [float(b["width"]) for b in bays],
        [float(b["height"]) for b in bays],
        float(W["w1"]), float(W["w2"]), float(W["w3"]))
    for i in range(n):
        for oi in range(len(B[i]["shape"])):
            L = [[list(v) for v in lay] for lay in B[i]["shape"][oi]["layers"]]
            rx, ry = L[0][0]
            eng.add_orient(i, [[(x - rx, y - ry) for (x, y) in lay]
                               for lay in L])
    eng.build_occ(max(D) + max(P) + 40)
    return eng


# the engine shifts every orientation so layer 0 vertex 0 sits at the
# origin; mirror that here or the geometry will not line up.
SHIFT = []
for i in range(n):
    s = []
    for oi in range(len(B[i]["shape"])):
        v = B[i]["shape"][oi]["layers"][0][0]
        s.append((v[0], v[1]))
    SHIFT.append(s)

_poly = {}


def layers(i, oi, x, y):
    """shapely polygons per layer for block i, orientation oi, at (x,y)"""
    key = (i, oi)
    if key not in _poly:
        rx, ry = SHIFT[i][oi]
        _poly[key] = [Polygon([(px - rx, py - ry) for (px, py) in lay])
                      for lay in B[i]["shape"][oi]["layers"]]
    from shapely import affinity
    return [affinity.translate(p, x, y) for p in _poly[key]]


_bbc = {}


def bbox(i, oi, x, y):
    key = (i, oi)
    if key not in _bbc:
        rx, ry = SHIFT[i][oi]
        vs = [v for lay in B[i]["shape"][oi]["layers"] for v in lay]
        _bbc[key] = (min(v[0] for v in vs) - rx, min(v[1] for v in vs) - ry,
                     max(v[0] for v in vs) - rx, max(v[1] for v in vs) - ry)
    a, b, c2, d2 = _bbc[key]
    return (a + x, b + y, c2 + x, d2 + y)



eng = build_engine()
flat = [0] * (n * 7)
for t, ops in board["operations"].items():
    for op in ops:
        if op["type"] == "ENTRY":
            i = op["block_id"]
            e = int(t)
            flat[i*7:i*7+7] = [op["bay_id"], op["orient_idx"], op["x"],
                               op["y"], e, e + P[i], 1]
eng.set_solution(flat)
eng.build_occ(max(D) + max(P) + 40)
z1, z2, z3, base = eng.objective()
print(json.dumps({"p": pnum, "incumbent": base, "z1": z1, "z2": z2,
                  "z3": z3}), flush=True)

# ---- pick the neighbourhood: the worst tardy blocks and whoever shares
# ---- their bay-time window (those are the only blocks that can trade)
tardy = sorted((i for i in range(n) if flat[i*7+6] and flat[i*7+5] > D[i]),
               key=lambda i: -(flat[i*7+5] - D[i]))
if not tardy:
    print(json.dumps({"note": "no tardy blocks"})); raise SystemExit
# WHOLE (bay, window) COLUMN, not a hand-picked set. With K=15 nearest
# neighbours the exact optimum equalled the incumbent on both boards, so
# the neighbourhood -- not the search -- was the limit. Free every block
# resident in the bay-window that carries the most tardiness; that is the
# set which can actually trade space-time among themselves.
best_win = None
for j in range(len(bays)):
    for t0w in range(0, max(D) + 1):
        occ = [i for i in range(n) if flat[i*7+6] and flat[i*7] == j
               and flat[i*7+4] < t0w + WIN and t0w < flat[i*7+5]]
        if not occ or len(occ) > K:
            continue
        late = sum(max(0, flat[i*7+5] - D[i]) for i in occ)
        if best_win is None or late > best_win[0]:
            best_win = (late, j, t0w, occ)
if best_win is None:
    print(json.dumps({"note": "no window under K"})); raise SystemExit
free = best_win[3]
print(json.dumps({"window_bay": best_win[1], "window_t0": best_win[2],
                  "window_tardy_days": best_win[0]}), flush=True)
print(json.dumps({"freed": len(free), "tardy_total": len(tardy)}), flush=True)

# ---- enumerate candidates against the FIXED part of the board
part = list(flat)
for i in free:
    part[i*7+6] = 0
eng.set_solution(part)
eng.build_occ(max(D) + max(P) + 40)

t0 = time.time()
GRID = int(os.environ.get("GRID", "0"))
CAND = {}
for i in free:
    if GRID > 0:
        # NON-CORNER candidates. feasible_positions only ever returns
        # corner placements (cornerSetsRB), so the whole solver -- and
        # therefore the CP-SAT neighbourhood built on it -- can only
        # express corner layouts. If the exact optimum over a grid-swept
        # candidate set beats the exact optimum over corners, then the
        # candidate GENERATOR, not the search, is the ceiling.
        cs = []
        for j in range(len(bays)):
            bw, bh = int(bays[j]["width"]), int(bays[j]["height"])
            for oi in range(len(B[i]["shape"])):
                x0, y0, x1, y1 = bbox(i, oi, 0, 0)
                for e in range(R[i], min(D[i] - P[i], max(D)) + 1):
                    for x in range(int(-x0), int(bw - x1) + 1, GRID):
                        for y in range(int(-y0), int(bh - y1) + 1, GRID):
                            if eng.place_at(i, j, oi, x, y, e):
                                cs.append((j, oi, x, y, e))
                                eng.set_solution(part)
                                eng.build_occ(max(D) + max(P) + 40)
                            if len(cs) >= NCAND:
                                break
                        if len(cs) >= NCAND:
                            break
                    if len(cs) >= NCAND:
                        break
                if len(cs) >= NCAND:
                    break
            if len(cs) >= NCAND:
                break
    else:
        raw = eng.feasible_positions(i, NCAND, 0)
        cs = [tuple(raw[q:q+5]) for q in range(0, len(raw), 5)]
    cur = tuple(flat[i*7:i*7+5])
    if cur not in cs:
        cs.append(cur)                 # never lose the incumbent option
    CAND[i] = cs
tc = time.time() - t0
sizes = [len(CAND[i]) for i in free]
print(json.dumps({"cand_secs": round(tc, 1), "cand_min": min(sizes),
                  "cand_max": max(sizes),
                  "cand_mean": round(sum(sizes)/len(sizes), 1)}), flush=True)


def cost(i, c):
    j, oi, x, y, e = c
    return (W["w1"] * max(0, e + P[i] - D[i])
            + W["w3"] * (SMAX[i] - PREF[i][j]))


def conflict(i, ci, k, ck):
    """optimistic pairwise screen: rest collision + crane sweep both ways"""
    j1, o1, x1, y1, e1 = ci
    j2, o2, x2, y2, e2 = ck
    if j1 != j2:
        return False
    f1, f2 = e1 + P[i], e2 + P[k]
    if not (e1 < f2 and e2 < f1):
        return False
    # bbox prefilter: shapely intersections dominate the screen and a
    # box test rejects almost all pairs for a few hundred nanoseconds
    ax0, ay0, ax1, ay1 = bbox(i, o1, x1, y1)
    bx0, by0, bx1, by1 = bbox(k, o2, x2, y2)
    if ax1 <= bx0 or bx1 <= ax0 or ay1 <= by0 or by1 <= ay0:
        return False
    La = layers(i, o1, x1, y1)
    Lb = layers(k, o2, x2, y2)
    for l in range(min(len(La), len(Lb))):          # rule 1: same layer
        if La[l].intersection(Lb[l]).area > 1e-9:
            return True
    # crane: the later entrant descends vertically past the resident, so
    # its layer l must clear the resident's layers l' >= l
    late, early = (Lb, La) if e2 >= e1 else (La, Lb)
    for l in range(len(late)):
        for l2 in range(l, len(early)):
            if late[l].intersection(early[l2]).area > 1e-9:
                return True
    return False


mdl = cp_model.CpModel()
V = {}
for i in free:
    vs = [mdl.NewBoolVar(f"z{i}_{q}") for q in range(len(CAND[i]))]
    mdl.AddExactlyOne(vs)
    V[i] = vs

t0 = time.time()
CELL = int(os.environ.get("CELL", "0"))
if CELL > 0:
    # CELL-DAY CAPACITY instead of pairwise clauses. The pairwise screen
    # is O(candidates^2) and took 48-107s at K=20-30, which is what caps
    # the neighbourhood -- CP-SAT itself closed those in under 7s. Since
    # 20-30 blocks proved exactly optimal, the open question is whether a
    # 60-80 block neighbourhood finds anything, and only a model that
    # builds LINEARLY in the candidate count can go there. Cells sample
    # the polygon, so this model is approximate; the vp re-verification
    # below is what keeps the answer honest.
    from collections import defaultdict
    occ_map = defaultdict(list)
    for i in free:
        for q, (j, oi, x, y, e) in enumerate(CAND[i]):
            Ls = layers(i, oi, x, y)
            for l, poly in enumerate(Ls):
                pp = prep(poly)
                x0, y0, x1, y1 = poly.bounds
                # a cell counts only if the POLYGON covers its centre.
                # Filling the bounding box instead made the model reject
                # the incumbent outright (INFEASIBLE): these boards run
                # 103-105% bbox utilisation against 58-65% polygon, i.e.
                # blocks sit deep inside each other's boxes, so a
                # box-filled cell map forbids packings that are perfectly
                # legal.
                for cx in range(int(x0 // CELL), int(x1 // CELL) + 1):
                    for cy in range(int(y0 // CELL), int(y1 // CELL) + 1):
                        if not pp.contains(Point((cx + 0.5) * CELL,
                                                 (cy + 0.5) * CELL)):
                            continue
                        for d in range(e, e + P[i]):
                            occ_map[(j, l, cx, cy, d)].append(V[i][q])
    ncons = 0
    for key, lits in occ_map.items():
        if len(lits) > 1:
            mdl.AddAtMostOne(lits)
            ncons += 1
    tp = time.time() - t0
    print(json.dumps({"cell_secs": round(tp, 1), "cell_cons": ncons,
                      "cells": len(occ_map)}), flush=True)
else:
    npairs = 0
    for a in range(len(free)):
        for b in range(a + 1, len(free)):
            i, k = free[a], free[b]
            for q, ci in enumerate(CAND[i]):
                for r, ck in enumerate(CAND[k]):
                    if conflict(i, ci, k, ck):
                        mdl.AddBoolOr([V[i][q].Not(), V[k][r].Not()])
                        npairs += 1
    tp = time.time() - t0
    print(json.dumps({"conflict_secs": round(tp, 1), "conflicts": npairs}),
          flush=True)

mdl.Minimize(sum(cost(i, CAND[i][q]) * V[i][q]
                 for i in free for q in range(len(CAND[i]))))

solver = cp_model.CpSolver()
solver.parameters.max_time_in_seconds = TL
solver.parameters.num_search_workers = 4

# LAZY FEASIBILITY CUTS. The cell map samples polygon centres and says
# nothing about the crane, so a solution can look better than it is --
# the first K=48 solve on P4 came back 3.15% "better" with 24 vp
# violations. Verify with the engine's oracle, turn every genuinely
# conflicting pair into a clause, and re-solve until the answer survives
# verification. The model may be approximate; the answer is not.
best = None
for it in range(8):
    t0 = time.time()
    st = solver.Solve(mdl)
    ts = time.time() - t0
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        print(json.dumps({"iter": it, "status": solver.StatusName(st)}),
              flush=True)
        break
    pick = {}
    for i2 in free:
        for q, ci in enumerate(CAND[i2]):
            if solver.Value(V[i2][q]):
                pick[i2] = (q, ci)
                break
    new = list(part)
    for i2, (q, ci) in pick.items():
        j, oi, x, y, e = ci
        new[i2*7:i2*7+7] = [j, oi, x, y, e, e + P[i2], 1]
    eng.set_solution(new)
    eng.build_occ(max(D) + max(P) + 40)
    bad = [i2 for i2 in range(n) if new[i2*7+6] and not eng.vp_ok(i2)]
    z1b, z2b, z3b, tot = eng.objective()
    print(json.dumps({"iter": it, "status": solver.StatusName(st),
                      "solve_secs": round(ts, 1), "obj": tot,
                      "delta_pct": round(100*(tot-base)/base, 2),
                      "vp_violations": len(bad)}), flush=True)
    if not bad:
        best = tot
        break
    ncut = 0
    badset = set(bad)
    for a2 in free:
        for b2 in free:
            if a2 >= b2:
                continue
            if a2 not in badset and b2 not in badset:
                continue
            qa, ca = pick[a2]
            qb, cb = pick[b2]
            if conflict(a2, ca, b2, cb):
                mdl.AddBoolOr([V[a2][qa].Not(), V[b2][qb].Not()])
                ncut += 1
    if ncut == 0:      # violation not explained pairwise -> forbid it all
        mdl.AddBoolOr([V[i2][pick[i2][0]].Not() for i2 in badset])
        ncut = 1
    print(json.dumps({"iter": it, "cuts_added": ncut}), flush=True)

print(json.dumps({"final": best, "incumbent": base,
                  "delta_pct": (round(100*(best-base)/base, 2)
                                if best is not None else None)}), flush=True)
