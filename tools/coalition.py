"""Minimum blocker-coalition size per tardy block.

census.py answered "is there a free punctual position?" -- the answer was
no, everywhere. The follow-up question decides the whole search design:
to open ONE punctual position for a tardy block, how many resident blocks
must be displaced?

If that minimum is 1-3, a random-k destroy can stumble onto the right
coalition and a blocker-directed ejection operator will nail it. If it is
8+, no local search over this neighbourhood reaches punctuality and the
construction itself has to change.

For every tardy block, over every (bay, orientation, x, y) and a few
punctual entry times, we count the DISTINCT residents whose per-layer
footprint overlaps, and report the minimum. Crane legality is ignored, so
the number is a lower bound on the true displacement cost.

Usage: coalition.py <pnum>
"""
import json
import math
import os
import sys

import numpy as np


CRANE = os.environ.get("CRANE", "1") != "0"


def xcorr(A, K, W, Hh):
    """overlap count per candidate position, valid region.

    Resting collision only needs SAME-layer overlap (j == k). Crane
    ENTRY is far stricter: descent is vertical, so the new block's
    layer k must clear the resident's layers j >= k -- an upper layer
    of a neighbour blocks the shaft even when nothing would touch at
    rest. The first census measured resting only, which is why it
    reported punctual positions one displacement away that neither op9
    nor the rescue move could ever use. CRANE=0 restores the old
    resting-only count for comparison.
    """
    S = (W, Hh)
    FA = np.fft.rfft2(A, s=S, axes=(-2, -1))
    FK = np.fft.rfft2(K, s=S, axes=(-2, -1))
    acc = None
    nl = K.shape[0]
    for k in range(nl):
        if not K[k].any():
            continue
        for j in (range(k, nl) if CRANE else (k,)):
            C = np.fft.irfft2(FA[j] * np.conj(FK[k]), s=S)
            acc = C if acc is None else acc + C
    if acc is None:
        return np.zeros((W - K.shape[1] + 1, Hh - K.shape[2] + 1))
    return acc[:W - K.shape[1] + 1, :Hh - K.shape[2] + 1]

pnum = int(sys.argv[1])
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

prob = json.load(open(f"data/preliminary_test/prob_{pnum}.json"))
best = json.load(open(f"runs/mipboards/prob_{pnum}.sol.json"))
B = prob["blocks"]
bays = prob["bays"]
P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
R = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]
D = [int(round(b["due_date"])) for b in B]

pl = {}
for t, ops in best["operations"].items():
    for op in ops:
        if op["type"] == "ENTRY":
            pl[op["block_id"]] = (op["bay_id"], op["orient_idx"],
                                  op["x"], op["y"], int(t))


def pip(px, py, poly):
    c = False
    for k in range(len(poly)):
        ax, ay = poly[k]
        bx, by = poly[(k + 1) % len(poly)]
        if (ay > py) != (by > py):
            xi = ax + (py - ay) * (bx - ax) / (by - ay)
            if px < xi:
                c = not c
    return c


_rc = {}


def raster(i, o):
    """per-layer cell sets in block-local integer coords"""
    key = (i, o)
    if key in _rc:
        return _rc[key]
    out = []
    for L in B[i]["shape"][o]["layers"]:
        xs = [v[0] for v in L]
        ys = [v[1] for v in L]
        cells = set()
        for cx in range(int(math.floor(min(xs))), int(math.ceil(max(xs)))):
            for cy in range(int(math.floor(min(ys))), int(math.ceil(max(ys)))):
                if pip(cx + 0.5, cy + 0.5, L):
                    cells.add((cx, cy))
        out.append(cells)
    _rc[key] = out
    return out


def kernel(cellsets, nlay):
    """stack per-layer cell sets into one (nlay, kw, kh) array + origin"""
    allc = [c for s in cellsets for c in s]
    if not allc:
        return None, 0, 0
    cx0 = min(c[0] for c in allc)
    cy0 = min(c[1] for c in allc)
    kw = max(c[0] for c in allc) - cx0 + 1
    kh = max(c[1] for c in allc) - cy0 + 1
    K = np.zeros((nlay, kw, kh), dtype=np.float32)
    for l, s in enumerate(cellsets):
        if l >= nlay:
            break
        for (cx, cy) in s:
            K[l, cx - cx0, cy - cy0] = 1.0
    return K, cx0, cy0


tardy = [i for i in pl if pl[i][4] + P[i] > D[i]]
tardy.sort(key=lambda i: -(pl[i][4] + P[i] - D[i]))
tot_days = sum(pl[i][4] + P[i] - D[i] for i in tardy)
print(json.dumps({"p": pnum, "tardy_blocks": len(tardy),
                  "tardy_days": tot_days}), flush=True)

NLAY = max(len(B[i]["shape"][o]["layers"])
           for i in range(len(B)) for o in range(len(B[i]["shape"])))

hist = {}
for i in tardy:
    late = pl[i][4] + P[i] - D[i]
    if D[i] - P[i] < R[i]:            # cannot be punctual at all
        hist["impossible"] = hist.get("impossible", 0) + 1
        print(json.dumps({"blk": i, "late": late, "min_blockers": None,
                          "v": "IMPOSSIBLE"}), flush=True)
        continue
    ents = sorted({R[i], D[i] - P[i], (R[i] + D[i] - P[i]) // 2})
    bestk = None
    for tb in range(len(bays)):
        W = int(bays[tb]["width"])
        Hh = int(bays[tb]["height"])
        for e0 in ents:
            f0 = e0 + P[i]
            res = [r for r, (rb, ro, rx, ry, re_) in pl.items()
                   if r != i and rb == tb and re_ < f0 and re_ + P[r] > e0]
            if not res:
                bestk = 0
                break
            # one occupancy grid per resident, per layer
            grids = []
            for r in res:
                A = np.zeros((NLAY, W, Hh), dtype=np.float32)
                rb, ro, rx, ry, _ = pl[r]
                for l, cells in enumerate(raster(r, ro)):
                    if l >= NLAY:
                        break
                    for (cx, cy) in cells:
                        gx, gy = rx + cx, ry + cy
                        if 0 <= gx < W and 0 <= gy < Hh:
                            A[l, gx, gy] = 1.0
                grids.append(A)
            for o in range(len(B[i]["shape"])):
                K, cx0, cy0 = kernel(raster(i, o), NLAY)
                if K is None or K.shape[1] > W or K.shape[2] > Hh:
                    continue
                total = None
                for A in grids:
                    hit = (xcorr(A, K, W, Hh) > 0.5).astype(np.int16)
                    total = hit if total is None else total + hit
                if total is None or total.size == 0:
                    continue
                k = int(total.min())
                if bestk is None or k < bestk:
                    bestk = k
                if bestk == 0:
                    break
            if bestk == 0:
                break
        if bestk == 0:
            break
    key = str(bestk) if bestk is not None and bestk <= 6 else "7+"
    hist[key] = hist.get(key, 0) + 1
    print(json.dumps({"blk": i, "late": late, "min_blockers": bestk}),
          flush=True)

print(json.dumps({"p": pnum, "coalition_hist": hist}), flush=True)
