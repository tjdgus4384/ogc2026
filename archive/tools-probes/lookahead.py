"""Lookahead-reservation constructor v4: optimistic-filter + exact
oracle verification.

v1 (center-raster only): raw tardy 113 on P4 (vs incumbent 179,
rank-1-implied ~135-143) but checker-infeasible (sliver overlaps +
sweep rules). v3 (conservative touch-raster + sweep model): feasible
but tardy 545 (over-dilation destroyed density). v4 keeps v1's dense
enumeration and verifies candidates with the ENGINE's vp oracle
(checker-exact placement rules, microsecond-scale) before commit:
feasible-by-construction at near-v1 density.

Greedy time-forward, releases known offline: place each block ASAP at
the vp-legal position minimizing feasible-position loss for the next
LOOKN releases; defer a day only when nothing is vp-legal.
Usage: lookahead.py <pnum> [LOOKN] [SAMP]
"""
import json
import math
import os
import sys

pnum = int(sys.argv[1])
LOOKN = int(sys.argv[2]) if len(sys.argv) > 2 else 8
SAMP = int(sys.argv[3]) if len(sys.argv) > 3 else 60
VTRY = int(os.environ.get("VTRY", "40"))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, "solver")
import myalgorithm as M          # noqa: E402
import utils                     # noqa: E402

prob = json.load(open(f"data/preliminary_test/prob_{pnum}.json"))
B = prob["blocks"]
bays = prob["bays"]
n = len(B)
m = len(bays)
P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
R = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]
D = [int(round(b["due_date"])) for b in B]
PR = [b["bay_preferences"] for b in B]

H = max(D) + max(P) + 60
eng = M._build_engine(prob)
eng.build_occ(H)
flat = [0, 0, 0, 0, 0, 0, 0] * n


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
    key = (i, o)
    if key in _rc:
        return _rc[key]
    out = []
    for L in B[i]["shape"][o]["layers"]:
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


occ = [dict() for _ in range(m)]
placed = {}


def cells_at(i, o, x, y):
    return [{(x + cx, y + cy) for (cx, cy) in cells}
            for cells in raster(i, o)]


def merged(tb, e, f, extra=None):
    out = {}
    for l, lay in occ[tb].items():
        u = set()
        for t in range(e, min(f, H)):
            ot = lay.get(t)
            if ot:
                u |= ot
        if u:
            out[l] = u
    if extra is not None:
        cs_list, ee, ff = extra
        if ee < f and e < ff:
            for l, cs in enumerate(cs_list):
                out.setdefault(l, set()).update(cs)
    return out


def positions(i, tb, e, f, cap=200, mg=None):
    W, Hh = bays[tb]["width"], bays[tb]["height"]
    if mg is None:
        mg = merged(tb, e, f)
    out = []
    for o in range(len(B[i]["shape"])):
        ras = raster(i, o)
        vs = [v for L in B[i]["shape"][o]["layers"] for v in L]
        x0 = min(v[0] for v in vs); x1 = max(v[0] for v in vs)
        y0 = min(v[1] for v in vs); y1 = max(v[1] for v in vs)
        for x in range(int(math.ceil(-x0)), int(math.floor(W - x1)) + 1):
            for y in range(int(math.ceil(-y0)),
                           int(math.floor(Hh - y1)) + 1):
                ok = True
                for l, cells in enumerate(ras):
                    ml = mg.get(l)
                    if not ml:
                        continue
                    hit = False
                    for (cx, cy) in cells:
                        if (x + cx, y + cy) in ml:
                            hit = True
                            break
                    if hit:
                        ok = False
                        break
                if ok:
                    out.append((o, x, y))
                    if len(out) >= cap:
                        return out
    return out


def stamp(i, o, x, y, tb, e, f):
    for l, cs in enumerate(cells_at(i, o, x, y)):
        lay = occ[tb].setdefault(l, {})
        for t in range(e, min(f, H)):
            lay.setdefault(t, set()).update(cs)


def vp_legal(i, tb, o, x, y, e, f):
    """engine oracle: candidate i AND every coexisting same-bay
    resident must stay vp-legal."""
    flat[i * 7:i * 7 + 7] = [tb, o, x, y, e, f, 1]
    eng.set_solution(flat)
    ok = eng.vp_ok(i)
    if ok:
        for r, v in placed.items():
            if v[0] == tb and v[4] < f and e < v[5]:
                if not eng.vp_ok(r):
                    ok = False
                    break
    if not ok:
        flat[i * 7:i * 7 + 7] = [0, 0, 0, 0, 0, 0, 0]
    return ok


order = sorted(range(n), key=lambda i: (R[i], D[i]))
import random
random.seed(7)
for idx, i in enumerate(order):
    e = R[i]
    placed_ok = False
    while not placed_ok:
        f = e + P[i]
        cands = []
        for tb in sorted(range(m), key=lambda b: -PR[i][b]):
            for (o, x, y) in positions(i, tb, e, f):
                cands.append((tb, o, x, y))
            if cands:
                break
        if not cands:
            e += 1
            continue
        future = [j for j in order[idx + 1:idx + 1 + LOOKN]]
        if len(cands) > SAMP:
            cands = random.sample(cands, SAMP)
        tb0 = cands[0][0]
        base_mg = {j: merged(tb0, R[j], R[j] + P[j]) for j in future
                   if R[j] < f and e < R[j] + P[j]}
        far = [j for j in future if j not in base_mg]
        base_loss = 0
        for j in far:
            pj = positions(j, tb0, R[j], R[j] + P[j], cap=6)
            base_loss += max(0, 6 - len(pj))
        scored = []
        for (tb, o, x, y) in cands:
            csl = cells_at(i, o, x, y)
            loss = base_loss
            for j, bm in base_mg.items():
                mgj = dict(bm)
                for l, cs in enumerate(csl):
                    mgj[l] = (mgj[l] | cs) if l in mgj else cs
                pj = positions(j, tb, R[j], R[j] + P[j], cap=6, mg=mgj)
                loss += max(0, 6 - len(pj))
            scored.append(((loss, -PR[i][tb], x + y), (tb, o, x, y)))
        scored.sort(key=lambda t: t[0])
        for _, (tb, o, x, y) in scored[:VTRY]:
            if vp_legal(i, tb, o, x, y, e, f):
                stamp(i, o, x, y, tb, e, f)
                placed[i] = [tb, x, y, o, e, f]
                placed_ok = True
                break
        if not placed_ok:
            e += 1

sol = M._recs_to_sol({i: [v[0], v[1], v[2], v[3], v[4], v[5]]
                      for i, v in placed.items()})
r = utils.check_feasibility(prob, sol)
td = sum(max(0, placed[i][4] + P[i] - D[i]) for i in placed)
print(json.dumps({"feasible": r.get("feasible"),
                  "objective": r.get("objective"),
                  "tardy_days_raw": td,
                  "violations": len(r.get("violations", []))}),
      flush=True)
cur = json.load(open(f"runs/mipboards/prob_{pnum}.sol.json"))
rc = utils.check_feasibility(prob, cur)
if r.get("feasible") and r["objective"] < rc["objective"]:
    json.dump(sol, open(f"runs/mipboards/prob_{pnum}.sol.json", "w"))
    print(json.dumps({"fed": r["objective"]}), flush=True)
else:
    json.dump(sol, open(f"runs/lookahead_p{pnum}.sol.json", "w"))
    print(json.dumps({"saved_aside": r.get("objective"),
                      "incumbent": rc["objective"]}), flush=True)
