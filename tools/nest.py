#!/usr/bin/env python3
"""SOTA nesting on the realiser benchmark: overlap minimisation, not construction.

The measurements say the constructor's position choice is the wall: with bay and
entry day pinned to an annealed board -- a schedule that board proves realisable
-- greedy seats 94/150 (P4) and 135/200 (P5), and neither insertion order nor
beam width nor the density mode moves it. What is missing is the ability to UNDO
a placement.

The literature axis here is irregular packing (nesting), because the blocks are
general rotatable polygons, not rectangles -- so CP-SAT's AddNoOverlap2D would
work on bounding boxes and be STRICTER than reality, rejecting valid packings.
The SOTA for nesting is not constructive at all: allow overlap, minimise total
overlap area by local search, stop at zero (Umetani et al.; Elkeran). That is
the principled form of "placement that can be undone".

The hot operation is "where does this piece overlap least", and evaluating it
position-by-position is hopeless in Python. The standard trick is rasterise +
FFT cross-correlation: one transform gives the overlap area at EVERY position.

Geometry is honoured as the kernel defines it (crane.hpp:109): layers are
STACKING LEVELS and two blocks conflict only at the same layer index, up to
min(nA, nB) -- so a tall block may sit over a short one. Rasters are therefore
per layer. Two blocks interact only if they share a bay AND their [e, f)
intervals overlap.

Ground truth: total overlap 0 means the schedule is realised. A raster solution
is then re-checked exactly by the kernel (polygon overlap + vertical passage),
since the raster is a discretisation and vp is not modelled here.

    python3 tools/nest.py prob_4 480 7 0.5 4000
"""
import hashlib
import json
import pathlib
import sys
import time

import numpy as np

REPO = pathlib.Path(__file__).resolve().parent.parent


def cached_board(pid, tl, seed):
    sd = str(REPO / "solver")
    so = pathlib.Path(sd) / "sscore.cpython-312-x86_64-linux-gnu.so"
    h = hashlib.sha256(f"{sd}|{so.stat().st_mtime_ns}".encode()).hexdigest()[:12]
    p = REPO / "runs" / "boards" / f"{pid}_tl{int(tl)}_s{seed}_{h}.json"
    if not p.exists():
        sys.exit(f"no cached board {p.name}")
    return json.loads(p.read_text())["base"]


def rasterise(poly, res, ox, oy, W, H):
    """Boolean mask of a polygon on a res-grid, origin (ox, oy), size W x H.

    Cell centres tested with shapely.contains_xy, which is vectorised -- doing
    it cell-by-cell would be ~4M shapely calls per instance.
    """
    import shapely
    from shapely.geometry import Polygon
    g = Polygon(poly)
    if not g.is_valid:
        g = g.buffer(0)
    xs = ox + (np.arange(W) + 0.5) * res
    ys = oy + (np.arange(H) + 0.5) * res
    X, Y = np.meshgrid(xs, ys)
    return shapely.contains_xy(g, X.ravel(), Y.ravel()).reshape(H, W)


def main():
    pid = sys.argv[1]
    tl = float(sys.argv[2])
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 7
    RES = float(sys.argv[4]) if len(sys.argv) > 4 else 0.5
    ITERS = int(sys.argv[5]) if len(sys.argv) > 5 else 4000
    ds = next(d for d in ("preliminary_test", "train_final", "train")
              if (REPO / "data" / d / f"{pid}.json").exists())
    prob = json.load(open(REPO / "data" / ds / f"{pid}.json"))
    B, bays = prob["blocks"], prob["bays"]
    n, m = len(B), len(bays)
    base = cached_board(pid, tl, seed)
    bay = [base[i * 7] for i in range(n)]
    ori0 = [base[i * 7 + 1] for i in range(n)]
    gx = [base[i * 7 + 2] for i in range(n)]
    gy = [base[i * 7 + 3] for i in range(n)]
    e = [base[i * 7 + 4] for i in range(n)]
    f = [base[i * 7 + 5] for i in range(n)]
    print(f"{pid} s{seed}: n={n} bays={[(b['width'],b['height']) for b in bays]}"
          f" res={RES}", flush=True)

    # Only the orientations the board actually uses are rasterised: baking all
    # eight per block costs 8x for nothing at this stage.
    t0 = time.time()
    MASK = {}
    for i in range(n):
        lays = []
        for lay in B[i]["shape"][ori0[i]]["layers"]:
            pts = [(q[0], q[1]) for q in lay]
            xs = [q[0] for q in pts]; ys = [q[1] for q in pts]
            w = int(np.ceil((max(xs) - min(xs)) / RES)) + 2
            h = int(np.ceil((max(ys) - min(ys)) / RES)) + 2
            lays.append((rasterise(pts, RES, min(xs), min(ys), w, h),
                         min(xs), min(ys)))
        MASK[i] = lays
    print(f"   rasterised {n} used shapes  [{time.time()-t0:.1f}s]", flush=True)

    # SANITY: the board that produced this schedule must show ZERO overlap in
    # the raster model. Stamp every bay/day/layer once and count cells covered
    # twice -- O(days x layers x grid), not O(n x conflicts).
    GW = [int(np.ceil(b["width"] / RES)) + 2 for b in bays]
    GH = [int(np.ceil(b["height"] / RES)) + 2 for b in bays]
    NL = max(len(v) for v in MASK.values())
    days = range(min(e), max(f) + 1)
    dbl = 0; cells = 0; oob = 0; worst = [0, 0]
    for j in range(m):
        here = [i for i in range(n) if bay[i] == j and base[i * 7 + 6]]
        for d in days:
            live = [i for i in here if e[i] <= d < f[i]]
            if len(live) < 2:
                continue
            for l in range(NL):
                cv = np.zeros((GH[j], GW[j]), dtype=np.int16)
                for i in live:
                    L = MASK[i]
                    if l >= len(L):
                        continue
                    mk, rx, ry = L[l]
                    px = int(round((gx[i] + rx) / RES))
                    py = int(round((gy[i] + ry) / RES))
                    hh, ww = mk.shape
                    if px < 0 or py < 0 or px+ww > GW[j] or py+hh > GH[j]:
                        oob += 1
                        worst[0] = min(worst[0], px, py)
                        worst[1] = max(worst[1], px+ww-GW[j], py+hh-GH[j])
                        continue
                    cv[py:py+hh, px:px+ww] += mk
                    cells += int(mk.sum())
                dbl += int((cv > 1).sum())
    print(f"   ground-truth overlap: {dbl:,} doubly-covered cells out of"
          f" {cells:,} stamped ({100*dbl/max(1,cells):.2f}%), {oob} out-of-bay"
          f"  [{time.time()-t0:.1f}s]", flush=True)
    print(f"   out-of-bay extent: min index {worst[0]}, overshoot {worst[1]}"
          f" cells (RES={RES}) -- a few cells is rounding, more is a"
          f" coordinate-convention error", flush=True)
    ok = 100.0 * dbl / max(1, cells) < 0.10 and worst[1] <= 1
    print(f"   => raster model {'REPRODUCES' if ok else 'DOES NOT reproduce'}"
          f" the known-feasible board", flush=True)
    if not ok:
        return

    # ---------------- SOTA nesting: minimise overlap, do not construct -------
    # All blocks are placed from the start (overlap allowed) and the search
    # relocates the worst offender to its minimum-overlap pose. The position
    # step is an FFT cross-correlation, so one transform scores EVERY position
    # in the bay at once -- the reason this scales where per-position polygon
    # tests do not.
    import random
    rng = random.Random(12345)
    ALL = {}
    for i in range(n):
        for o, sh in enumerate(B[i]["shape"]):
            lays = []
            for lay in sh["layers"]:
                pts = [(q[0], q[1]) for q in lay]
                xs = [q[0] for q in pts]; ys = [q[1] for q in pts]
                w = int(np.ceil((max(xs)-min(xs))/RES))+2
                h = int(np.ceil((max(ys)-min(ys))/RES))+2
                lays.append((rasterise(pts, RES, min(xs), min(ys), w, h)
                             .astype(np.float32), min(xs), min(ys)))
            ALL[(i, o)] = lays

    inbay = [[] for _ in range(m)]
    for i in range(n):
        inbay[bay[i]].append(i)
    conf = {i: [j for j in inbay[bay[i]] if j != i and e[j] < f[i] and e[i] < f[j]]
            for i in range(n)}
    # start from scratch: everything at the bay's bottom-left, maximal overlap
    P = {i: (0, 0, 0) for i in range(n)}          # (ori, px, py)

    def field(i, l, skip):
        fl = np.zeros((GH[bay[i]], GW[bay[i]]), dtype=np.float32)
        for j in conf[i]:
            if j == skip:
                continue
            oj, pxj, pyj = P[j]
            L = ALL[(j, oj)]
            if l >= len(L):
                continue
            mk, rx, ry = L[l]
            hh, ww = mk.shape
            if pxj < 0 or pyj < 0 or pxj+ww > fl.shape[1] or pyj+hh > fl.shape[0]:
                continue
            fl[pyj:pyj+hh, pxj:pxj+ww] += mk
        return fl

    def score_at(i, o, fields):
        L = ALL[(i, o)]
        acc = None
        for l, (mk, rx, ry) in enumerate(L):
            fl = fields[l] if l < len(fields) else None
            if fl is None:
                continue
            H, W = fl.shape; hh, ww = mk.shape
            if hh > H or ww > W:
                return None
            F = np.fft.rfft2(fl, s=(H, W))
            Mk = np.fft.rfft2(mk[::-1, ::-1], s=(H, W))
            c = np.fft.irfft2(F*Mk, s=(H, W))[hh-1:H, ww-1:W]
            acc = c if acc is None else acc + c[:acc.shape[0], :acc.shape[1]]
        return acc

    def total():
        t = 0.0
        for i in range(n):
            o, px, py = P[i]
            L = ALL[(i, o)]
            for l, (mk, rx, ry) in enumerate(L):
                fl = field(i, l, None)
                hh, ww = mk.shape
                if py+hh > fl.shape[0] or px+ww > fl.shape[1]:
                    return 1e18
                t += float((fl[py:py+hh, px:px+ww]*mk).sum())
        return t / 2.0

    t1 = time.time()
    order = list(range(n))
    best = None
    for it in range(ITERS):
        i = order[it % n]
        if it % n == 0:
            rng.shuffle(order)
        fields = [field(i, l, i) for l in range(NL)]
        bo = None
        for o in range(len(B[i]["shape"])):
            c = score_at(i, o, fields)
            if c is None or c.size == 0:
                continue
            k = int(np.argmin(c)); v = float(c.flat[k])
            py, px = divmod(k, c.shape[1])
            if bo is None or v < bo[0]:
                bo = (v, o, px, py)
        if bo is not None:
            P[i] = (bo[1], bo[2], bo[3])
        if it % n == n - 1:
            tt = total()
            print(f"      sweep {it//n+1}: total overlap {tt:,.0f} cells"
                  f"  [{time.time()-t1:.0f}s]", flush=True)
            if best is None or tt < best:
                best = tt
            if tt <= 0:
                break
    print(f"   nesting result: overlap {best:,.0f} cells"
          + ("   <-- REALISES THE SCHEDULE" if best is not None and best <= 0
             else "   [did not reach zero]")
          + f"  [{time.time()-t1:.0f}s]", flush=True)


if __name__ == "__main__":
    main()
