"""Iterated CP-SAT LNS: does an EXACT repair beat our greedy one?

Two measured facts point here and nowhere else. First, the incumbent is
exactly optimal over the most congested 20-30 block window (cpsat_lns.py,
delta 0.0% under corner AND arbitrary-grid candidates, vp_violations 0).
Second, widening the ALNS ruin past that window makes things worse
(+3.6%/+2.1%) -- not because the bigger neighbourhood is empty, but
because the greedy first-fit repair cannot rebuild 50 blocks well.

So the untested combination is a large ruin with an EXACT repair. The
earlier probes only ever re-solved the single most congested window of
the incumbent, which is precisely the set already known to be optimal.
This loops over RANDOM subsets instead: free K blocks anywhere on the
board, re-place them optimally against the frozen rest, keep the result
if the true objective improves, repeat.

If every subset returns the incumbent, the board is optimal over
essentially all K-subsets and this axis is closed for good. If some
subset improves, we have a working optimizer and the only question left
is how much of it fits in the time limit.

Usage: cpsat_loop.py <pnum> [K] [ncand] [iters] [solve_tl]
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

from ortools.sat.python import cp_model
from shapely import affinity
from shapely.geometry import Polygon

import sscore

pnum = int(sys.argv[1])
K = int(sys.argv[2]) if len(sys.argv) > 2 else 15
NCAND = int(sys.argv[3]) if len(sys.argv) > 3 else 30
ITERS = int(sys.argv[4]) if len(sys.argv) > 4 else 60
STL = float(sys.argv[5]) if len(sys.argv) > 5 else 10.0
rng = random.Random(12345)

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
HOR = max(D) + max(P) + 40

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

SHIFT = [[tuple(B[i]["shape"][oi]["layers"][0][0])
          for oi in range(len(B[i]["shape"]))] for i in range(n)]
_poly, _bbc = {}, {}


def layers(i, oi, x, y):
    key = (i, oi)
    if key not in _poly:
        rx, ry = SHIFT[i][oi]
        _poly[key] = [Polygon([(px - rx, py - ry) for (px, py) in lay])
                      for lay in B[i]["shape"][oi]["layers"]]
    return [affinity.translate(p, x, y) for p in _poly[key]]


def bbox(i, oi, x, y):
    key = (i, oi)
    if key not in _bbc:
        rx, ry = SHIFT[i][oi]
        vs = [v for lay in B[i]["shape"][oi]["layers"] for v in lay]
        _bbc[key] = (min(v[0] for v in vs) - rx, min(v[1] for v in vs) - ry,
                     max(v[0] for v in vs) - rx, max(v[1] for v in vs) - ry)
    a, b_, c_, d_ = _bbc[key]
    return (a + x, b_ + y, c_ + x, d_ + y)


def conflict(i, ci, k, ck):
    j1, o1, x1, y1, e1 = ci
    j2, o2, x2, y2, e2 = ck
    if j1 != j2:
        return False
    f1, f2 = e1 + P[i], e2 + P[k]
    if not (e1 < f2 and e2 < f1):
        return False
    ax0, ay0, ax1, ay1 = bbox(i, o1, x1, y1)
    bx0, by0, bx1, by1 = bbox(k, o2, x2, y2)
    if ax1 <= bx0 or bx1 <= ax0 or ay1 <= by0 or by1 <= ay0:
        return False
    La, Lb = layers(i, o1, x1, y1), layers(k, o2, x2, y2)
    for l in range(min(len(La), len(Lb))):
        if La[l].intersection(Lb[l]).area > 1e-9:
            return True
    late, early = (Lb, La) if e2 >= e1 else (La, Lb)
    for l in range(len(late)):
        for l2 in range(l, len(early)):
            if late[l].intersection(early[l2]).area > 1e-9:
                return True
    return False


def cost(i, c):
    j, oi, x, y, e = c
    return (W["w1"] * max(0, e + P[i] - D[i])
            + W["w3"] * (SMAX[i] - PREF[i][j]))


cur = [0] * (n * 7)
for t, ops in board["operations"].items():
    for op in ops:
        if op["type"] == "ENTRY":
            i = op["block_id"]
            e = int(t)
            cur[i*7:i*7+7] = [op["bay_id"], op["orient_idx"], op["x"],
                              op["y"], e, e + P[i], 1]
eng.set_solution(cur)
eng.build_occ(HOR)
base = eng.objective()[3]
best = base
print(json.dumps({"p": pnum, "incumbent": base, "K": K,
                  "ncand": NCAND, "iters": ITERS}), flush=True)

_PEERS = []
if os.environ.get("PEERS", "0") == "1":
    import glob as _g
    for _f in sorted(_g.glob(f"runs/mine-*/prob_{pnum}.sol.json")):
        try:
            _b = json.load(open(_f))
            _fl = [0] * (n * 7)
            for _t, _ops in _b["operations"].items():
                for _op in _ops:
                    if _op["type"] == "ENTRY":
                        _i = _op["block_id"]; _e = int(_t)
                        _fl[_i*7:_i*7+7] = [_op["bay_id"], _op["orient_idx"],
                                            _op["x"], _op["y"], _e,
                                            _e + P[_i], 1]
            _PEERS.append(_fl)
        except Exception:
            pass
    print(json.dumps({"peer_boards": len(_PEERS)}), flush=True)

t_start = time.time()
improved = tried = 0
for it in range(ITERS):
    tardy = [i for i in range(n) if cur[i*7+6] and cur[i*7+5] > D[i]]
    if not tardy:
        break
    # seed the subset on a tardy block, then take a random sample of the
    # rest -- a purely uniform subset almost never contains the blocks
    # whose tardiness is what we are trying to trade away.
    seed_i = rng.choice(tardy)
    pool = [i for i in range(n) if cur[i*7+6] and i != seed_i]
    free = [seed_i] + rng.sample(pool, min(K - 1, len(pool)))

    part = list(cur)
    for i in free:
        part[i*7+6] = 0
    eng.set_solution(part)
    eng.build_occ(HOR)

    CAND = {}
    GRID = int(os.environ.get("GRID", "0"))
    PEERS = globals().get("_PEERS")
    for i in free:
        raw = eng.feasible_positions(i, NCAND, 0)
        cs = [tuple(raw[q:q+5]) for q in range(0, len(raw), 5)]
        if GRID > 0:
            # NON-CORNER candidates. The 07-31 matheuristic PROVED the
            # gap lives outside the corner vocabulary (P3: no
            # recombination of known placements beats the incumbent,
            # OPTIMAL in 129s). The working subset re-solver still only
            # eats corners; this feeds it grid positions too.
            extra = 0
            days = {R[i], max(R[i], min(D[i]-P[i], cur[i*7+4]))}
            for j in range(len(bays)):
                bw, bh = int(bays[j]["width"]), int(bays[j]["height"])
                for e in sorted(days):
                    for x in range(0, bw, GRID):
                        for y in range(0, bh, GRID):
                            if extra >= 20: break
                            if eng.place_at(i, j, 0, x, y, e):
                                cs.append((j, 0, x, y, e))
                                extra += 1
                                eng.set_solution(part)
                                eng.build_occ(HOR)
                        if extra >= 20: break
                    if extra >= 20: break
                if extra >= 20: break
        if PEERS:
            # CROSS-BASIN VOCABULARY. Grid positions added nothing
            # (refuted on P4 and P6) -- but those were arbitrary
            # coordinates. These are the positions block i actually
            # occupies in boards mined from four different config
            # families, i.e. placements other basins certified as part
            # of a full packing. Each must still hold against THIS
            # board's frozen rest, so verify with place_at.
            seen = set(cs)
            for pb_sol in PEERS:
                c = tuple(pb_sol[i*7:i*7+5])
                if c in seen: continue
                seen.add(c)
                if eng.place_at(i, c[0], c[1], c[2], c[3], c[4]):
                    cs.append(c)
                    eng.set_solution(part)
                    eng.build_occ(HOR)
        c0 = tuple(cur[i*7:i*7+5])
        if c0 not in cs:
            cs.append(c0)
        CAND[i] = cs

    mdl = cp_model.CpModel()
    V = {}
    for i in free:
        vs = [mdl.NewBoolVar(f"z{i}_{q}") for q in range(len(CAND[i]))]
        mdl.AddExactlyOne(vs)
        V[i] = vs
    # conflicts from the engine (C++, exact) rather than shapely: this
    # was 2-3s of every 2-3s iteration, and every reduction in it has
    # moved the measured verdict.
    flatc, idx = [], []
    for i in free:
        for q, (j, oi, x, y, e) in enumerate(CAND[i]):
            flatc += [i, j, oi, x, y, e]
            idx.append((i, q))
    prs = eng.pair_conflicts(flatc)
    for t in range(0, len(prs), 2):
        (i, q), (k, r) = idx[prs[t]], idx[prs[t + 1]]
        mdl.AddBoolOr([V[i][q].Not(), V[k][r].Not()])
    if it == 0:
        print(json.dumps({"conflicts": len(prs)//2,
                          "cands": len(idx)}), flush=True)
    mdl.Minimize(sum(cost(i, CAND[i][q]) * V[i][q]
                     for i in free for q in range(len(CAND[i]))))
    sv = cp_model.CpSolver()
    sv.parameters.max_time_in_seconds = STL
    sv.parameters.num_search_workers = 4
    st = sv.Solve(mdl)
    tried += 1
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        continue

    new = list(part)
    for i in free:
        for q, ci in enumerate(CAND[i]):
            if sv.Value(V[i][q]):
                j, oi, x, y, e = ci
                new[i*7:i*7+7] = [j, oi, x, y, e, e + P[i], 1]
                break
    eng.set_solution(new)
    eng.build_occ(HOR)
    if any(new[i*7+6] and not eng.vp_ok(i) for i in range(n)):
        eng.set_solution(cur)
        eng.build_occ(HOR)
        continue
    val = eng.objective()[3]
    if val < best - 1e-9:
        improved += 1
        best = val
        cur = new
        print(json.dumps({"iter": it, "obj": val,
                          "delta_pct": round(100*(val-base)/base, 3)}),
              flush=True)
    else:
        eng.set_solution(cur)
        eng.build_occ(HOR)

print(json.dumps({"p": pnum, "tried": tried, "improved": improved,
                  "incumbent": base, "final": best,
                  "delta_pct": round(100*(best-base)/base, 3),
                  "wall": round(time.time() - t_start, 1)}), flush=True)
