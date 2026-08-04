"""Overlap-minimization probe: can separation reach punctual packings
feasible search provably cannot?

Method class from the nesting literature (Umetani 2009 GLS overlap
minimization; Imamichi 2009 swap+separation): search PASSES THROUGH the
infeasible space -- force tardy blocks onto punctual entries with
overlap allowed, then minimize total penetration by translating blocks,
with GLS penalties on stubborn pairs. Our campaign proved feasible-only
search is locally optimal everywhere; this is the one method class that
does not respect that boundary.

Probe question only: starting from the seed-7 board with every tardy
block RETIMED to its punctual entry (overlaps appear), can translation
separation drive total overlap to zero -- and for how many blocks?
Crane rules are ignored during separation and checked at the end (vp).

Usage: separate.py <pnum> [sweeps]
"""
import json
import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "solver"))
os.chdir(ROOT)

from shapely import affinity
from shapely.geometry import Polygon
import sscore

pnum = int(sys.argv[1])
SWEEPS = int(sys.argv[2]) if len(sys.argv) > 2 else 400

prob = json.load(open(f"data/preliminary_test/prob_{pnum}.json"))
board = json.load(open(f"runs/v41-s7/prob_{pnum}.sol.json"))
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

st = [0] * (n * 7)     # bay,ori,x,y,e,f,placed
for t, ops in board["operations"].items():
    for op in ops:
        if op["type"] == "ENTRY":
            i = op["block_id"]; e = int(t)
            st[i*7:i*7+7] = [op["bay_id"], op["orient_idx"], op["x"],
                             op["y"], e, e + P[i], 1]
eng.set_solution(st)
eng.build_occ(HOR)
z1a = eng.objective()[1]

SHIFT = [[tuple(B[i]["shape"][oi]["layers"][0][0])
          for oi in range(len(B[i]["shape"]))] for i in range(n)]
_base = {}


def polys(i):
    oi = st[i*7+1]
    key = (i, oi)
    if key not in _base:
        rx, ry = SHIFT[i][oi]
        _base[key] = [Polygon([(px - rx, py - ry) for (px, py) in lay])
                      for lay in B[i]["shape"][oi]["layers"]]
    return [affinity.translate(p, st[i*7+2], st[i*7+3]) for p in _base[key]]


# retime every tardy block to its punctual entry (overlaps now allowed)
tardy = [i for i in range(n) if st[i*7+6] and st[i*7+5] > D[i]
         and D[i] - P[i] >= R[i]]
for i in tardy:
    e = D[i] - P[i]
    st[i*7+4] = e; st[i*7+5] = e + P[i]
print(json.dumps({"p": pnum, "board_z1": z1a, "retimed": len(tardy)}),
      flush=True)


def overlap_pairs():
    """same-bay, time-overlapping, same-layer intersection area > 0"""
    out = {}
    bybay = {}
    for i in range(n):
        if st[i*7+6]:
            bybay.setdefault(st[i*7], []).append(i)
    for j, ids in bybay.items():
        for a in range(len(ids)):
            for b in range(a + 1, len(ids)):
                i, k = ids[a], ids[b]
                if not (st[i*7+4] < st[k*7+5] and st[k*7+4] < st[i*7+5]):
                    continue
                Li, Lk = polys(i), polys(k)
                ar = sum(Li[l].intersection(Lk[l]).area
                         for l in range(min(len(Li), len(Lk))))
                if ar > 1e-9:
                    out[(i, k)] = ar
    return out


pen = {}
t0 = time.time()
ov = overlap_pairs()
print(json.dumps({"initial_pairs": len(ov),
                  "initial_area": round(sum(ov.values()), 1)}), flush=True)
STEPS = [(dx, dy) for dx in (-6, -3, -1, 0, 1, 3, 6)
         for dy in (-6, -3, -1, 0, 1, 3, 6) if (dx, dy) != (0, 0)]
for sweep in range(SWEEPS):
    if not ov:
        break
    # move the block with the largest penalized overlap
    load = {}
    for (i, k), a in ov.items():
        w = a * (1.0 + pen.get((i, k), 0.0))
        load[i] = load.get(i, 0.0) + w
        load[k] = load.get(k, 0.0) + w
    mover = max(load, key=load.get)
    j = st[mover*7]
    bw, bh = bays[j]["width"], bays[j]["height"]
    oi = st[mover*7+1]
    rx, ry = SHIFT[mover][oi]
    vs = [v for lay in B[mover]["shape"][oi]["layers"] for v in lay]
    x0 = min(v[0] for v in vs) - rx; x1 = max(v[0] for v in vs) - rx
    y0 = min(v[1] for v in vs) - ry; y1 = max(v[1] for v in vs) - ry
    bx, by = st[mover*7+2], st[mover*7+3]

    def cost_at(x, y):
        st[mover*7+2] = x; st[mover*7+3] = y
        c = 0.0
        Li = polys(mover)
        for k2 in range(n):
            if k2 == mover or not st[k2*7+6] or st[k2*7] != j:
                continue
            if not (st[mover*7+4] < st[k2*7+5] and st[k2*7+4] < st[mover*7+5]):
                continue
            Lk = polys(k2)
            a = sum(Li[l].intersection(Lk[l]).area
                    for l in range(min(len(Li), len(Lk))))
            if a > 1e-9:
                key = (min(mover, k2), max(mover, k2))
                c += a * (1.0 + pen.get(key, 0.0))
        return c

    best = cost_at(bx, by); bxy = (bx, by)
    for dx, dy in STEPS:
        x, y = bx + dx, by + dy
        if x + x0 < 0 or y + y0 < 0 or x + x1 > bw or y + y1 > bh:
            continue
        c = cost_at(x, y)
        if c < best - 1e-9:
            best = c; bxy = (x, y)
    st[mover*7+2], st[mover*7+3] = bxy
    if bxy == (bx, by):
        # stuck: GLS-penalize this block's overlap pairs
        for (i, k) in list(ov):
            if mover in (i, k):
                pen[(i, k)] = pen.get((i, k), 0.0) + 0.4
    ov = overlap_pairs()
    if sweep % 50 == 0:
        print(json.dumps({"sweep": sweep, "pairs": len(ov),
                          "area": round(sum(ov.values()), 1)}), flush=True)

eng.set_solution(st)
eng.build_occ(HOR)
bad = [i for i in range(n) if st[i*7+6] and not eng.vp_ok(i)]
z1b = eng.objective()[1]
print(json.dumps({"final_pairs": len(ov),
                  "final_area": round(sum(ov.values()), 1),
                  "z1_now": z1b, "z1_was": z1a,
                  "vp_violations": len(bad),
                  "wall": round(time.time() - t0, 1)}), flush=True)
