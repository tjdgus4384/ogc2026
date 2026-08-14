#!/usr/bin/env python3
"""The crane-LEGAL contact density: a greedy lower bound on the ceiling.

The four-link chain leaves one number unmeasured, and the whole direction
stands or falls on it: how much polygon fill can a contact packing reach WHILE
passing vpConservative? Blind bottom-left contact reaches 53-65% on the raster
but the kernel rejects 47-68% of it; the shipped pipeline reaches 49-57%.

Greedy legalisation measures a lower bound in one job: pack the peak scene
contact-wise, but for each block walk the free contact anchors in bottom-left
order and take the FIRST the kernel accepts (place_at = exact five rules,
original staggered entry days). The raster stays in sync with accepted seats
only, so later blocks see the true legal occupancy.

  ceiling ~ 49-57%  -> the crane tax eats the prize; the axis DIES
  ceiling >= 60%    -> the design investment is justified

Arms: corner baseline (same procedure, kernel corner candidates) vs
legal-contact. Extras (tardy blocks) pushed after residents, same as repack.

    python3 tools/legalpack.py prob_6 900 7
"""
import hashlib, json, pathlib, sys
import numpy as np
from shapely.geometry import Polygon
import shapely
REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "builds" / "ordf"))
RES = 1.0
TRYCAP = 400


def cached(pid, tl, seed):
    sd = str(REPO / "solver")
    so = pathlib.Path(sd) / "sscore.cpython-312-x86_64-linux-gnu.so"
    h = hashlib.sha256(f"{sd}|{so.stat().st_mtime_ns}".encode()).hexdigest()[:12]
    p = REPO / "runs" / "boards" / f"{pid}_tl{int(tl)}_s{seed}_{h}.json"
    return json.loads(p.read_text())["base"]


def rast(pts):
    g = Polygon(pts)
    if not g.is_valid:
        g = g.buffer(0)
    minx, miny, maxx, maxy = g.bounds
    w = int(np.ceil((maxx-minx)/RES)) + 1
    h = int(np.ceil((maxy-miny)/RES)) + 1
    xs = minx + (np.arange(w)+0.5)*RES
    ys = miny + (np.arange(h)+0.5)*RES
    X, Y = np.meshgrid(xs, ys)
    return (shapely.contains_xy(g, X.ravel(), Y.ravel()).reshape(h, w),
            minx, miny, g.area)


def freepos(occ, mk):
    H, W = occ.shape; h, w = mk.shape
    if h > H or w > W:
        return None
    F = np.fft.rfft2(occ.astype(np.float32), s=(H, W))
    M = np.fft.rfft2(mk[::-1, ::-1].astype(np.float32), s=(H, W))
    c = np.fft.irfft2(F*M, s=(H, W))[h-1:H, w-1:W]
    return c < 0.5


def main():
    pid, tl = sys.argv[1], float(sys.argv[2])
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 7
    ds = next(d for d in ("preliminary_test","train_final","train")
              if (REPO/"data"/d/f"{pid}.json").exists())
    prob = json.load(open(REPO/"data"/ds/f"{pid}.json"))
    B, bays = prob["blocks"], prob["bays"]
    n, m = len(B), len(bays)
    import myalgorithm as ma
    eng, D, P = ma._fresh(prob, 4)
    hz = eng.horizon()
    cur = cached(pid, tl, seed)
    tardy = [i for i in range(n) if cur[i*7+6] and cur[i*7+5] > D[i]]
    for j in range(m):
        days = {}
        for i in range(n):
            if cur[i*7+6] and cur[i*7] == j:
                for d in range(cur[i*7+4], cur[i*7+5]):
                    days.setdefault(d, []).append(i)
        if not days:
            continue
        d, live = max(days.items(), key=lambda kv: len(kv[1]))
        W, H = bays[j]["width"], bays[j]["height"]
        GW, GH = int(np.ceil(W/RES)), int(np.ceil(H/RES))
        extras = [i for i in tardy if i not in set(live)][:20]
        seq = sorted(live, key=lambda i: (cur[i*7+4], i))   # entry order
        seq += extras                                        # then extras at d

        def entry_of(i):
            return cur[i*7+4] if i in set(live) else d

        results = {}
        for mode in ("corner", "contact"):
            eng.set_solution([0]*(n*7)); eng.build_occ(hz)
            occ = np.zeros((GH, GW), dtype=bool)
            area = 0.0; cnt = 0; xcnt = 0
            for i in seq:
                oi = cur[i*7+1]
                mk, ox, oy, pa = rast([(q[0], q[1])
                                       for q in B[i]["shape"][oi]["layers"][0]])
                e0 = entry_of(i)
                seated = False
                if mode == "corner":
                    rows = eng.feasible_positions(i, 12, e0, j, e0)
                    for q in range(0, len(rows), 5):
                        b2, o2, x, y, e2 = rows[q:q+5]
                        if eng.place_at(i, b2, o2, x, y, e2):
                            seated = True
                            px = int(round((x + ox)/RES))
                            py = int(round((y + oy)/RES))
                            break
                else:
                    fp = freepos(occ, mk)
                    if fp is not None and fp.any():
                        ys, xs = np.nonzero(fp)
                        o2 = np.argsort(ys*10000 + xs)
                        for k in o2[:TRYCAP]:
                            py, px = int(ys[k]), int(xs[k])
                            x = int(round(px*RES - ox))
                            y = int(round(py*RES - oy))
                            if eng.place_at(i, j, oi, x, y, e0):
                                seated = True
                                break
                if seated:
                    hh, ww = mk.shape
                    if 0 <= py and 0 <= px and py+hh <= GH and px+ww <= GW:
                        occ[py:py+hh, px:px+ww] |= mk
                    cnt += 1; area += pa
                    if i in set(extras):
                        xcnt += 1
            results[mode] = (cnt, xcnt, area)
        c1, e1, a1 = results["corner"]; c2, e2, a2 = results["contact"]
        print(f"{pid} bay{j} day {d}: live {len(live)} + extras {len(extras)}",
              flush=True)
        print(f"   corner (kernel)       : seated {c1:>3} (extra {e1})"
              f"  LEGAL fill {100*a1/(W*H):.0f}%", flush=True)
        print(f"   legal-contact (kernel): seated {c2:>3} (extra {e2})"
              f"  LEGAL fill {100*a2/(W*H):.0f}%"
              + (f"   <-- +{100*(a2-a1)/(W*H):.0f}pp, +{e2-e1} extras"
                 if a2 > a1 else ""), flush=True)


if __name__ == "__main__":
    main()
