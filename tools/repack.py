#!/usr/bin/env python3
"""Static peak-day repack: does contact packing beat bbox-corner packing HERE?

The interlock hypothesis' decisive test. The realizer A/B was the wrong
instrument (+1..+2 seats): re-seating an annealed schedule never needs
interlock because that schedule was CHOSEN at bbox density. The hypothesis is
about reaching DENSER packings -- earlier entries, lower z1 -- than the
bbox-corner generator allows.

So pack the actual peak-day residents of each bay from empty, one at a time
(area-descending), under two placement rules:
  corner   candidate anchors = bay walls + residents' BBOX corner snugs
           (what cornerSetsRB produces), first feasible in (y, x) order
  contact  bottom-left-most free position of the TRUE polygon raster,
           found by FFT correlation over the occupancy grid (the validated
           nest.py machinery, res 0.5: ground-truth overlap 0.00%)
then keep inserting EXTRA blocks (the instance's tardy ones, area-desc) until
neither rule can seat another. Score = polygon fill achieved + extra blocks
seated. If contact barely beats corner, the bbox slack is geometrically
unavoidable for these (mostly convex) shapes and the hypothesis DIES. If it
seats materially more, the insertion vocabulary is the wall.

    python3 tools/repack.py prob_6 900 7
"""
import hashlib, json, pathlib, sys
import numpy as np
from shapely.geometry import Polygon
import shapely
REPO = pathlib.Path(__file__).resolve().parent.parent
RES = 0.5


def cached(pid, tl, seed):
    sd = str(REPO / "solver")
    so = pathlib.Path(sd) / "sscore.cpython-312-x86_64-linux-gnu.so"
    h = hashlib.sha256(f"{sd}|{so.stat().st_mtime_ns}".encode()).hexdigest()[:12]
    p = REPO / "runs" / "boards" / f"{pid}_tl{int(tl)}_s{seed}_{h}.json"
    return json.loads(p.read_text())["base"]


def rast(pts, W, H):
    g = Polygon(pts)
    if not g.is_valid:
        g = g.buffer(0)
    minx, miny, maxx, maxy = g.bounds
    w = int(np.ceil((maxx - minx) / RES)) + 1
    h = int(np.ceil((maxy - miny) / RES)) + 1
    xs = minx + (np.arange(w) + 0.5) * RES
    ys = miny + (np.arange(h) + 0.5) * RES
    X, Y = np.meshgrid(xs, ys)
    return (shapely.contains_xy(g, X.ravel(), Y.ravel()).reshape(h, w),
            minx, miny, g.area)


def free_positions(occ, mk):
    """boolean grid of anchor positions where mk fits with zero overlap."""
    H, W = occ.shape; h, w = mk.shape
    if h > H or w > W:
        return None
    F = np.fft.rfft2(occ.astype(np.float32), s=(H, W))
    M = np.fft.rfft2(mk[::-1, ::-1].astype(np.float32), s=(H, W))
    c = np.fft.irfft2(F * M, s=(H, W))[h-1:H, w-1:W]
    return c < 0.5


def main():
    pid, tl = sys.argv[1], float(sys.argv[2])
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 7
    ds = next(d for d in ("preliminary_test","train_final","train")
              if (REPO/"data"/d/f"{pid}.json").exists())
    prob = json.load(open(REPO/"data"/ds/f"{pid}.json"))
    B, bays = prob["blocks"], prob["bays"]
    n, m = len(B), len(bays)
    cur = cached(pid, tl, seed)
    D = [int(round(b["due_date"])) for b in B]
    tardy = [i for i in range(n) if cur[i*7+6] and cur[i*7+5] > D[i]]
    for j in range(m):
        W, H = bays[j]["width"], bays[j]["height"]
        GW, GH = int(np.ceil(W/RES)), int(np.ceil(H/RES))
        # peak day of this bay
        days = {}
        for i in range(n):
            if cur[i*7+6] and cur[i*7] == j:
                for d in range(cur[i*7+4], cur[i*7+5]):
                    days.setdefault(d, []).append(i)
        if not days:
            continue
        d, live = max(days.items(), key=lambda kv: len(kv[1]))
        extra = [i for i in tardy if i not in set(live)][:30]
        seq = sorted(live, key=lambda i: -Polygon(
            [(q[0], q[1]) for q in B[i]["shape"][cur[i*7+1]]["layers"][0]]
            ).area) + extra
        results = {}
        for mode in ("corner", "contact"):
            occ = np.zeros((GH, GW), dtype=bool)
            anchors = [(0, 0)]
            seated = area = 0.0; cnt = 0; extra_cnt = 0
            for idx, i in enumerate(seq):
                mk, ox, oy, parea = rast(
                    [(q[0], q[1]) for q in B[i]["shape"][cur[i*7+1]]["layers"][0]],
                    W, H)
                fp = free_positions(occ, mk)
                if fp is None or not fp.any():
                    continue
                if mode == "contact":
                    ys, xs = np.nonzero(fp)
                    k = np.argmin(ys * 10000 + xs)      # bottom-left
                    py, px = int(ys[k]), int(xs[k])
                else:
                    # bbox-corner anchors only: walls + placed bbox corners
                    best = None
                    for ay, ax in anchors:
                        if 0 <= ay < fp.shape[0] and 0 <= ax < fp.shape[1] \
                                and fp[ay, ax]:
                            if best is None or (ay, ax) < best:
                                best = (ay, ax)
                    if best is None:
                        continue
                    py, px = best
                occ[py:py+mk.shape[0], px:px+mk.shape[1]] |= mk
                bh, bw = mk.shape
                anchors += [(py, px+bw), (py+bh, px), (py+bh, px+bw)]
                cnt += 1; area += parea
                if idx >= len(live):
                    extra_cnt += 1
            results[mode] = (cnt, extra_cnt, area)
        c1, e1, a1 = results["corner"]; c2, e2, a2 = results["contact"]
        print(f"{pid} bay{j} ({W}x{H}) day {d}, residents {len(live)}"
              f" + extras {len(extra)}:", flush=True)
        print(f"   corner : seated {c1:>3} (extra {e1})  polygon fill"
              f" {100*a1/(W*H):.0f}%", flush=True)
        print(f"   contact: seated {c2:>3} (extra {e2})  polygon fill"
              f" {100*a2/(W*H):.0f}%"
              + (f"   <-- +{e2-e1} extra blocks" if e2 > e1 else ""),
              flush=True)


if __name__ == "__main__":
    main()
