"""Force-relaxation separator (nesting-literature style, rung 2).

Rung 1 (single-block grid moves) could not reduce overlap on interlocked
boards -- expected: separating interlocks needs SIMULTANEOUS motion.
This is the standard physics relaxation: for every overlapping pair,
approximate the minimum translation vector from the intersection's
extents, accumulate opposing forces on both blocks, then move ALL blocks
one damped step and repeat. GLS-style pair penalties kick stubborn pairs
harder. Positions are snapped back to integers each step (the problem is
integral); bay walls clamp.

Probe question: with every tardy block retimed to its punctual entry,
does total overlap reach (near) zero -- i.e., is the punctual packing
reachable by continuous deformation? Crane checked at the end.

Usage: separate2.py <pnum> [steps]
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
STEPS = int(sys.argv[2]) if len(sys.argv) > 2 else 1500

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
eng.set_solution(st)
eng.build_occ(HOR)
z1a = eng.objective()[0]

SHIFT = [[tuple(B[i]["shape"][oi]["layers"][0][0])
          for oi in range(len(B[i]["shape"]))] for i in range(n)]
_base = {}
POSX = [float(st[i*7+2]) for i in range(n)]     # continuous during relax
POSY = [float(st[i*7+3]) for i in range(n)]


def polys(i):
    oi = st[i*7+1]
    key = (i, oi)
    if key not in _base:
        rx, ry = SHIFT[i][oi]
        _base[key] = [Polygon([(px - rx, py - ry) for (px, py) in lay])
                      for lay in B[i]["shape"][oi]["layers"]]
    return [affinity.translate(p, POSX[i], POSY[i]) for p in _base[key]]


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


def pairs_and_area():
    tot = 0.0; out = []
    for j, ids in bybay.items():
        for a in range(len(ids)):
            for b in range(a + 1, len(ids)):
                i, k = ids[a], ids[b]
                if not (st[i*7+4] < st[k*7+5] and st[k*7+4] < st[i*7+5]):
                    continue
                Li, Lk = polys(i), polys(k)
                for l in range(min(len(Li), len(Lk))):
                    inter = Li[l].intersection(Lk[l])
                    if inter.area > 1e-9:
                        out.append((i, k, inter))
                        tot += inter.area
                        break
    return out, tot


pen = {}
t0 = time.time()
ov, area0 = pairs_and_area()
print(json.dumps({"initial_pairs": len(ov), "initial_area": round(area0, 1)}),
      flush=True)
DAMP = 0.6
best_area = area0
for step in range(STEPS):
    ov, area = pairs_and_area()
    if not ov:
        break
    fx = [0.0] * n; fy = [0.0] * n
    for (i, k, inter) in ov:
        x0, y0, x1, y1 = inter.bounds
        w, h = x1 - x0, y1 - y0
        keyp = (min(i, k), max(i, k))
        g = 1.0 + pen.get(keyp, 0.0)
        # push along the thinner axis of the intersection (approx MTV)
        cxi, cyi = POSX[i], POSY[i]
        cxk, cyk = POSX[k], POSY[k]
        if w <= h:
            d = (1.0 if cxi >= cxk else -1.0) * (w + 0.2) * 0.5 * g
            fx[i] += d; fx[k] -= d
        else:
            d = (1.0 if cyi >= cyk else -1.0) * (h + 0.2) * 0.5 * g
            fy[i] += d; fy[k] -= d
    moved = 0.0
    for i in range(n):
        if fx[i] == 0.0 and fy[i] == 0.0:
            continue
        x0, y0, x1, y1 = bbox_local(i)
        j = st[i*7]
        bw, bh = bays[j]["width"], bays[j]["height"]
        nx = min(max(POSX[i] + DAMP * fx[i], -x0), bw - x1)
        ny = min(max(POSY[i] + DAMP * fy[i], -y0), bh - y1)
        moved += abs(nx - POSX[i]) + abs(ny - POSY[i])
        POSX[i], POSY[i] = nx, ny
    if moved < 0.05:      # stuck: penalize all current pairs (GLS)
        for (i, k, _) in ov:
            keyp = (min(i, k), max(i, k))
            pen[keyp] = pen.get(keyp, 0.0) + 0.5
    if area < best_area:
        best_area = area
    if step % 100 == 0:
        print(json.dumps({"step": step, "pairs": len(ov),
                          "area": round(area, 1)}), flush=True)

# snap to integers and evaluate
for i in range(n):
    x0, y0, x1, y1 = bbox_local(i)
    j = st[i*7]
    bw, bh = bays[j]["width"], bays[j]["height"]
    st[i*7+2] = int(min(max(round(POSX[i]), math.ceil(-x0)), math.floor(bw - x1)))
    st[i*7+3] = int(min(max(round(POSY[i]), math.ceil(-y0)), math.floor(bh - y1)))
POSX = [float(st[i*7+2]) for i in range(n)]
POSY = [float(st[i*7+3]) for i in range(n)]
ov, area = pairs_and_area()
eng.set_solution(st)
eng.build_occ(HOR)
bad = [i for i in range(n) if st[i*7+6] and not eng.vp_ok(i)]
z1b = eng.objective()[0]
print(json.dumps({"final_pairs": len(ov), "final_area": round(area, 1),
                  "best_area_seen": round(best_area, 1),
                  "z1_retimed_board": z1b, "z1_original": z1a,
                  "vp_violations": len(bad),
                  "wall": round(time.time() - t0, 1)}), flush=True)
