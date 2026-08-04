"""Rung 3: monotone line-searched descent on global penetration.

Rung 2 diverged because raw force steps have no global objective
control. Same MTV-style directions, but now they form a descent
direction for F = sum_w pd(i,k)^2 and every step passes a backtracking
line search -- F decreases monotonically by construction. When descent
stalls above zero: GLS reweighting, and position-SWAPS of the stuck
pairs (Imamichi's move). Retime-to-punctual initialization as before.

Usage: separate3.py <pnum> [rounds]
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
ROUNDS = int(sys.argv[2]) if len(sys.argv) > 2 else 60

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

st = [0] * (n * 7)
for t, ops in board["operations"].items():
    for op in ops:
        if op["type"] == "ENTRY":
            i = op["block_id"]; e = int(t)
            st[i*7:i*7+7] = [op["bay_id"], op["orient_idx"], op["x"],
                             op["y"], e, e + P[i], 1]
eng.set_solution(st); eng.build_occ(HOR)
z1a = eng.objective()[0]

SHIFT = [[tuple(B[i]["shape"][oi]["layers"][0][0])
          for oi in range(len(B[i]["shape"]))] for i in range(n)]
_base = {}
X = [float(st[i*7+2]) for i in range(n)]
Y = [float(st[i*7+3]) for i in range(n)]


def polys(i, x=None, y=None):
    oi = st[i*7+1]
    key = (i, oi)
    if key not in _base:
        rx, ry = SHIFT[i][oi]
        _base[key] = [Polygon([(px - rx, py - ry) for (px, py) in lay])
                      for lay in B[i]["shape"][oi]["layers"]]
    return [affinity.translate(p, X[i] if x is None else x,
                               Y[i] if y is None else y) for p in _base[key]]


def bbox_local(i):
    oi = st[i*7+1]
    rx, ry = SHIFT[i][oi]
    vs = [v for lay in B[i]["shape"][oi]["layers"] for v in lay]
    return (min(v[0] for v in vs) - rx, min(v[1] for v in vs) - ry,
            max(v[0] for v in vs) - rx, max(v[1] for v in vs) - ry)


tardy = [i for i in range(n) if st[i*7+6] and st[i*7+5] > D[i]
         and D[i] - P[i] >= R[i]]
for i in tardy:
    e = D[i] - P[i]
    st[i*7+4] = e; st[i*7+5] = e + P[i]
print(json.dumps({"p": pnum, "board_z1": z1a, "retimed": len(tardy)}),
      flush=True)

bybay = {}
for i in range(n):
    if st[i*7+6]:
        bybay.setdefault(st[i*7], []).append(i)
cand_pairs = []
for j, ids in bybay.items():
    for a in range(len(ids)):
        for b in range(a + 1, len(ids)):
            i, k = ids[a], ids[b]
            if st[i*7+4] < st[k*7+5] and st[k*7+4] < st[i*7+5]:
                cand_pairs.append((i, k))
pen = {}


def F_and_G():
    F = 0.0
    gx = [0.0] * n; gy = [0.0] * n
    pairs = []
    for (i, k) in cand_pairs:
        Li, Lk = polys(i), polys(k)
        for l in range(min(len(Li), len(Lk))):
            inter = Li[l].intersection(Lk[l])
            if inter.area <= 1e-9:
                continue
            x0, y0, x1, y1 = inter.bounds
            w, h = x1 - x0, y1 - y0
            pd = min(w, h)
            gwt = 1.0 + pen.get((i, k), 0.0)
            F += gwt * pd * pd
            s = 1.0 if (X[i] - X[k]) * (0 if w > h else 1) + \
                        (Y[i] - Y[k]) * (0 if w <= h else 1) >= 0 else -1.0
            if w <= h:
                d = 2 * gwt * pd * (1.0 if X[i] >= X[k] else -1.0)
                gx[i] += d; gx[k] -= d
            else:
                d = 2 * gwt * pd * (1.0 if Y[i] >= Y[k] else -1.0)
                gy[i] += d; gy[k] -= d
            pairs.append((i, k, pd))
            break
    return F, gx, gy, pairs


def clampi(i, x, y):
    x0, y0, x1, y1 = bbox_local(i)
    j = st[i*7]
    return (min(max(x, -x0), bays[j]["width"] - x1),
            min(max(y, -y0), bays[j]["height"] - y1))


t0 = time.time()
F, gx, gy, pairs = F_and_G()
print(json.dumps({"initial_pairs": len(pairs), "initial_F": round(F, 1)}),
      flush=True)
hist = []
for rd in range(ROUNDS):
    if not pairs:
        break
    # descent with backtracking line search (monotone by construction)
    for _ in range(30):
        F, gx, gy, pairs = F_and_G()
        if not pairs:
            break
        alpha = 0.5
        ok = False
        for _bt in range(8):
            oldX, oldY = X[:], Y[:]
            for i in range(n):
                if gx[i] or gy[i]:
                    X[i], Y[i] = clampi(i, X[i] + alpha * gx[i] * 0.05,
                                        Y[i] + alpha * gy[i] * 0.05)
            F2, _, _, _ = F_and_G()
            if F2 < F - 1e-6:
                ok = True
                break
            X, Y = oldX, oldY
            alpha *= 0.5
        if not ok:
            break
    F, gx, gy, pairs = F_and_G()
    if not pairs:
        break
    # stalled above zero: GLS + swap the worst stuck pair
    worst = max(pairs, key=lambda t3: t3[2])
    i, k, _ = worst
    pen[(i, k)] = pen.get((i, k), 0.0) + 1.0
    X[i], X[k] = X[k], X[i]
    Y[i], Y[k] = Y[k], Y[i]
    X[i], Y[i] = clampi(i, X[i], Y[i])
    X[k], Y[k] = clampi(k, X[k], Y[k])
    if rd % 10 == 0:
        print(json.dumps({"round": rd, "pairs": len(pairs),
                          "F": round(F, 2)}), flush=True)
    hist.append(F)

for i in range(n):
    xi, yi = clampi(i, round(X[i]), round(Y[i]))
    st[i*7+2] = int(xi); st[i*7+3] = int(yi)
X = [float(st[i*7+2]) for i in range(n)]
Y = [float(st[i*7+3]) for i in range(n)]
F, _, _, pairs = F_and_G()
eng.set_solution(st); eng.build_occ(HOR)
bad = [i for i in range(n) if st[i*7+6] and not eng.vp_ok(i)]
z1b = eng.objective()[0]
print(json.dumps({"final_pairs": len(pairs), "final_F": round(F, 2),
                  "z1_retimed": z1b, "z1_original": z1a,
                  "vp_violations": len(bad),
                  "wall": round(time.time() - t0, 1)}), flush=True)
