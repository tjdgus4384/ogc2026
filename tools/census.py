"""Per-tardy-block early-entry feasibility census (layer-aware).

For each tardy block i on the incumbent board: enumerate stride-1
positions x orientations in its bay and its argmax-pref bay, and count
positions whose PER-LAYER footprint is disjoint from every resident
present during [R_i, R_i+P_i) (nesting-aware: different layers may
overlap in x-y). Says exactly which tardy days are geometrically
unlockable (order/timing problem) vs structural (no position exists).
Usage: census.py <pnum>
"""
import json
import math
import os
import sys

pnum = int(sys.argv[1])
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

prob = json.load(open(f"data/preliminary_test/prob_{pnum}.json"))
best = json.load(open(f"runs/mipboards/prob_{pnum}.sol.json"))
B = prob["blocks"]
n = len(B)
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
    """per-layer cell sets in block-local coords"""
    key = (i, o)
    if key in _rc:
        return _rc[key]
    lays = B[i]["shape"][o]["layers"]
    out = []
    for L in lays:
        xs = [v[0] for v in L]
        ys = [v[1] for v in L]
        cells = set()
        for cx in range(int(math.floor(min(xs))),
                        int(math.ceil(max(xs)))):
            for cy in range(int(math.floor(min(ys))),
                            int(math.ceil(max(ys)))):
                if pip(cx + 0.5, cy + 0.5, L):
                    cells.add((cx, cy))
        out.append(cells)
    _rc[key] = out
    return out


tardy = [i for i in pl if pl[i][4] + P[i] > D[i]]
tardy.sort(key=lambda i: -(pl[i][4] + P[i] - D[i]))
tot_days = sum(pl[i][4] + P[i] - D[i] for i in tardy)
print(json.dumps({"tardy_blocks": len(tardy), "tardy_days": tot_days}),
      flush=True)

unlockable = structural = 0
for i in tardy:
    late = pl[i][4] + P[i] - D[i]
    pr = B[i]["bay_preferences"]
    cand_bays = range(len(bays)) if os.environ.get("ALLBAYS") \
        else {pl[i][0], max(range(len(pr)), key=lambda j: pr[j])}
    e0, f0 = R[i], R[i] + P[i]
    found = 0
    for tb in cand_bays:
        W, Hh = bays[tb]["width"], bays[tb]["height"]
        # residents' per-layer occupied cells during the early window
        occ = {}          # layer -> set of cells
        for r, (rb, ro, rx, ry, re_) in pl.items():
            if r == i or rb != tb:
                continue
            if re_ >= f0 or re_ + P[r] <= e0:
                continue
            for l, cells in enumerate(raster(r, ro)):
                st = occ.setdefault(l, set())
                for (cx, cy) in cells:
                    st.add((rx + cx, ry + cy))
        for o in range(len(B[i]["shape"])):
            ras = raster(i, o)
            vs = [v for L in B[i]["shape"][o]["layers"] for v in L]
            x0 = min(v[0] for v in vs); x1 = max(v[0] for v in vs)
            y0 = min(v[1] for v in vs); y1 = max(v[1] for v in vs)
            for x in range(int(math.ceil(-x0)),
                           int(math.floor(W - x1)) + 1):
                for y in range(int(math.ceil(-y0)),
                               int(math.floor(Hh - y1)) + 1):
                    ok = True
                    for l, cells in enumerate(ras):
                        ol = occ.get(l)
                        if not ol:
                            continue
                        for (cx, cy) in cells:
                            if (x + cx, y + cy) in ol:
                                ok = False
                                break
                        if not ok:
                            break
                    if ok:
                        found += 1
                        if found >= 5:
                            break
                if found >= 5:
                    break
            if found >= 5:
                break
        if found >= 5:
            break
    status = "UNLOCKABLE" if found else "structural"
    if found:
        unlockable += late
    else:
        structural += late
    print(json.dumps({"blk": i, "late": late, "early_pos": found,
                      "v": status}), flush=True)
print(json.dumps({"unlockable_days": unlockable,
                  "structural_days": structural}), flush=True)
