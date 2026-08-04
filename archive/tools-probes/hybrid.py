"""Is an exact-repair polish worth the annealing time it costs?

The iterated CP-SAT LNS improves boards that local search provably
cannot (P6 -1.5% at K=15, -2.6% at K=25, still improving at iteration
337). But it only earns that on wall clock the anneal would otherwise
have used, so the honest question is not "does it improve a board" --
it does -- but "does the same second buy more here than in the anneal".

Both arms get the IDENTICAL total budget and the identical start:
  full   - the portfolio for the whole time limit
  polish - the portfolio for (1-f)*TL, then CP-SAT LNS for the rest

Usage: hybrid.py <pnum> <full|polish> <TL> [frac] [K] [ncand]
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
mode = sys.argv[2]
TL = float(sys.argv[3])
FRAC = float(sys.argv[4]) if len(sys.argv) > 4 else 0.35
K = int(sys.argv[5]) if len(sys.argv) > 5 else 25
NCAND = int(sys.argv[6]) if len(sys.argv) > 6 else 30
seed = int(os.environ.get("OGC_SEED", "7"))
rng = random.Random(seed * 7919 + 13)

prob = json.load(open(f"data/preliminary_test/prob_{pnum}.json"))
B = prob["blocks"]
bays = prob["bays"]
W = prob["weights"]
n = len(B)
P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
R = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]
D = [int(round(b["due_date"])) for b in B]
PREF = [[float(v) for v in b["bay_preferences"]] for b in B]
SMAX = [max(pr) for pr in PREF]
# P6 blocks run thousands of tardy days past their due dates,
# so a horizon of max(D)+max(P)+40 is too tight there and the
# construction silently returns an empty board.
HOR = int((max(D) + max(P)) * 2 + 200)

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
    if not (e1 < e2 + P[k] and e2 < e1 + P[i]):
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


t_all = time.time()
# try several construction orders until one places every block; a
# single order fails outright on the largest board.
orders = [sorted(range(n), key=lambda i: (D[i], -P[i])),
          sorted(range(n), key=lambda i: D[i]),
          sorted(range(n), key=lambda i: (R[i], D[i])),
          sorted(range(n), key=lambda i: (D[i] - P[i], -P[i]))]
rc = None
for od in orders:
    rc = eng.reconstruct_ordered([-1] * n, od, False, seed)
    if rc == -1:
        break
start = eng.get_solution()
nplaced = sum(start[i*7+6] for i in range(n))
print(json.dumps({"reconstruct_rc": rc, "start_placed": nplaced,
                  "n": n, "horizon": HOR}), flush=True)
if nplaced < n:
    print(json.dumps({"fatal": "construction incomplete"}), flush=True)
    raise SystemExit(1)

anneal = TL if mode == "full" else TL * (1.0 - FRAC)
eng.run_portfolio(4, anneal, 1, 0.05, 0.15, 0.002, 0.9997, seed, 1, [start], 0)
cur = eng.get_solution()
after_anneal = eng.objective()[3]
print(json.dumps({"p": pnum, "mode": mode, "anneal_secs": round(anneal, 1),
                  "after_anneal": after_anneal}), flush=True)

improved = tried = 0
best = after_anneal


def polish_slice(until):
    """run subset re-solves until `until`; returns (gain, tries, imps)"""
    global cur, best, improved, tried
    t_in, b_in, n_in = time.time(), best, tried
    while time.time() < until - 1:
        tardy = [i for i in range(n) if cur[i*7+6] and cur[i*7+5] > D[i]]
        if not tardy:
            break
        seed_i = rng.choice(tardy)
        pool = [i for i in range(n) if cur[i*7+6] and i != seed_i]
        free = [seed_i] + rng.sample(pool, min(K - 1, len(pool)))
        part = list(cur)
        for i in free:
            part[i*7+6] = 0
        eng.set_solution(part)
        eng.build_occ(HOR)
        CAND = {}
        for i in free:
            raw = eng.feasible_positions(i, NCAND, 0)
            cs = [tuple(raw[q:q+5]) for q in range(0, len(raw), 5)]
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
        # exact conflicts from the engine. In python this cost 2-3s of
        # every 2-3s iteration and the polish lost to the anneal; with
        # the C++ path P6 reached -4.06% in 214s where 1155s of the
        # python loop had managed -1.52%.
        flatc, idx = [], []
        for i in free:
            for q, (j, oi, x, y, e) in enumerate(CAND[i]):
                flatc += [i, j, oi, x, y, e]
                idx.append((i, q))
        prs = eng.pair_conflicts(flatc)
        for t2 in range(0, len(prs), 2):
            (i, q), (k, r) = idx[prs[t2]], idx[prs[t2 + 1]]
            mdl.AddBoolOr([V[i][q].Not(), V[k][r].Not()])
        mdl.Minimize(sum(cost(i, CAND[i][q]) * V[i][q]
                         for i in free for q in range(len(CAND[i]))))
        sv = cp_model.CpSolver()
        sv.parameters.max_time_in_seconds = min(
            10.0, max(1.0, deadline - time.time()))
        sv.parameters.num_search_workers = 4
        st = sv.Solve(mdl)
        tried += 1
        if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            eng.set_solution(cur); eng.build_occ(HOR); continue
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
            eng.set_solution(cur); eng.build_occ(HOR); continue
        val = eng.objective()[3]
        if val < best - 1e-9:
            best, cur, improved = val, new, improved + 1
        else:
            eng.set_solution(cur); eng.build_occ(HOR)

    return b_in - best, tried - n_in, time.time() - t_in


deadline = t_all + TL
if mode == "polish":
    polish_slice(deadline - 3)
elif mode == "auto":
    # TWO-ARMED SLICE ALLOCATION. At a fixed 30% split the polish wins P6
    # (-3.17%) but loses P4 (+3.01%) and is pure waste on P3, where it
    # never fires because nothing is tardy -- so no single fraction is
    # right and picking one per instance would fit the boards we can see.
    # Both arms are measured in gain-per-second on real slices and the
    # next slice goes to whichever is ahead, with a periodic re-probe so
    # an early unlucky slice cannot lock an arm out permanently.
    SLICE = TL * 0.06
    rate = {"p": None, "a": None}
    rounds = 0
    while time.time() < deadline - 5:
        rounds += 1
        if rate["p"] is None:
            arm = "p"
        elif rate["a"] is None:
            arm = "a"
        elif rounds % 5 == 0:
            arm = "a" if rate["p"] >= rate["a"] else "p"   # re-probe
        else:
            arm = "p" if rate["p"] >= rate["a"] else "a"
        end = min(deadline - 3, time.time() + SLICE)
        if arm == "p":
            g, _t, dt = polish_slice(end)
            rate["p"] = g / max(1e-9, dt)
        else:
            b0, t1 = best, time.time()
            eng.set_solution(cur); eng.build_occ(HOR)
            eng.run_portfolio(4, max(1.0, end - t1), 1, 0.05, 0.15,
                              0.002, 0.9997, seed + rounds, 1, [cur], 0)
            v = eng.objective()[3]
            if v < best:
                best, cur = v, eng.get_solution()
            rate["a"] = (b0 - best) / max(1e-9, time.time() - t1)
    print(json.dumps({"auto_final": best, "rate_polish": rate["p"],
                      "rate_anneal": rate["a"], "slices": rounds}), flush=True)

eng.set_solution(cur)
eng.build_occ(HOR)
z1, z2, z3, tot = eng.objective()
bad = [i for i in range(n) if cur[i*7+6] and not eng.vp_ok(i)]
print(json.dumps({"p": pnum, "mode": mode, "seed": seed,
                  "obj": tot, "z1": z1, "z2": z2, "z3": z3,
                  "polish_tried": tried, "polish_improved": improved,
                  "vp_violations": len(bad),
                  "wall": round(time.time() - t_all, 1)}), flush=True)
