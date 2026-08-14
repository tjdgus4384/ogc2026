#!/usr/bin/env python3
"""The decisive link: is a WHOLE contact packing crane-legal as an ensemble?

craneck killed single-block contact insertion on existing boards (0 gain --
bbox-slack pockets are fragmented). repack proved contact packing from empty
fills +13-19pp more area (raster only). The hypothesis now stands or falls on
ensemble legality: rebuild the peak scene CONTACT-PACKED and let the kernel
judge every placement -- residents keep their ORIGINAL staggered entry days
(so rule-2b batches stay realistic), only (x, y) move to the contact layout.
Disjoint on the peak day implies disjoint on every day (live sets are subsets).

  PASS (most blocks vp-accepted)  -> the lever is real but ensemble-level:
       construction and repair must be contact-based from the start
  FAIL (mass rejection)           -> the bbox slack is CRANE-mandated;
       the axis closes for good

    python3 tools/ensemble.py prob_6 900 7
"""
import hashlib, json, pathlib, sys
import numpy as np
from shapely.geometry import Polygon
import shapely
REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "builds" / "ordf"))
RES = 1.0


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
    w = int(np.ceil((maxx - minx) / RES)) + 1
    h = int(np.ceil((maxy - miny) / RES)) + 1
    xs = minx + (np.arange(w) + 0.5) * RES
    ys = miny + (np.arange(h) + 0.5) * RES
    X, Y = np.meshgrid(xs, ys)
    return (shapely.contains_xy(g, X.ravel(), Y.ravel()).reshape(h, w),
            minx, miny)


def freepos(occ, mk):
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
    import myalgorithm as ma
    eng, D, P = ma._fresh(prob, 4)
    hz = eng.horizon()
    cur = cached(pid, tl, seed)
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
        # contact-pack the live set (area-desc) on the raster, layer-0
        occ = np.zeros((GH, GW), dtype=bool)
        pose = {}
        order = sorted(live, key=lambda i: -Polygon(
            [(q[0], q[1]) for q in B[i]["shape"][cur[i*7+1]]["layers"][0]]
            ).area)
        for i in order:
            mk, ox, oy = rast([(q[0], q[1])
                               for q in B[i]["shape"][cur[i*7+1]]["layers"][0]])
            fp = freepos(occ, mk)
            if fp is None or not fp.any():
                continue
            ys, xs = np.nonzero(fp)
            k = np.argmin(ys * 10000 + xs)
            py, px = int(ys[k]), int(xs[k])
            occ[py:py+mk.shape[0], px:px+mk.shape[1]] |= mk
            # kernel anchor: x,y such that layer0 min corner sits at grid pos
            pose[i] = (int(round(px*RES - ox)), int(round(py*RES - oy)))
        # replay through the kernel: original staggered (e, f), contact (x, y),
        # in entry order; place_at judges with the full five rules
        eng.set_solution([0]*(n*7)); eng.build_occ(hz)
        okc = failc = 0
        for i in sorted(pose, key=lambda i: (cur[i*7+4], i)):
            x, y = pose[i]
            if eng.place_at(i, j, cur[i*7+1], x, y, cur[i*7+4]):
                okc += 1
            else:
                failc += 1
        print(f"{pid} bay{j} day {d}: live {len(live)}, contact-packed"
              f" {len(pose)}, kernel-accepted {okc}, rejected {failc}"
              f"  ({100*okc/max(1,len(pose)):.0f}% legal)"
              + ("   <-- ENSEMBLE PASSES" if failc <= len(pose)//10 else
                 "   <-- CRANE REJECTS THE PACKING" if failc > len(pose)//3
                 else ""), flush=True)


if __name__ == "__main__":
    main()
