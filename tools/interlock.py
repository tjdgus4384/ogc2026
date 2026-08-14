#!/usr/bin/env python3
"""The bbox-slack signature: how much area is trapped, and is interlock legal?

repair.hpp:317 records the signature: boards are bbox-bound (bbox utilisation
103%) while polygons fill 58-65%. If real, ~35-40% of the yard is trapped in
the gap between each polygon and its bounding box -- the only quantity today
whose magnitude matches the reference gap (+21-47%), and z1bound proved the
area would suffice for zero tardiness if usable.

Premises to verify on the cached annealed boards, before building anything:
 1. the utilisation numbers themselves (per bay, peak day)
 2. legality: same-layer polygon-DISJOINT is the resting rule; bbox overlap is
    legal. Verify by measurement: count resident pairs whose bboxes overlap in
    time+space (existing interlock) -- if > 0 on a checker-feasible board,
    interlock is legal by example.
 3. headroom: on the peak day of each bay, polygon area vs bay area.

    python3 tools/interlock.py prob_4 480 7
"""
import hashlib, json, pathlib, sys
from shapely.geometry import Polygon, box
REPO = pathlib.Path(__file__).resolve().parent.parent


def cached(pid, tl, seed):
    sd = str(REPO / "solver")
    so = pathlib.Path(sd) / "sscore.cpython-312-x86_64-linux-gnu.so"
    h = hashlib.sha256(f"{sd}|{so.stat().st_mtime_ns}".encode()).hexdigest()[:12]
    p = REPO / "runs" / "boards" / f"{pid}_tl{int(tl)}_s{seed}_{h}.json"
    if not p.exists():
        sys.exit(f"no cached board {p.name}")
    return json.loads(p.read_text())["base"]


def main():
    pid, tl = sys.argv[1], float(sys.argv[2])
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 7
    ds = next(d for d in ("preliminary_test","train_final","train")
              if (REPO/"data"/d/f"{pid}.json").exists())
    prob = json.load(open(REPO/"data"/ds/f"{pid}.json"))
    B, bays = prob["blocks"], prob["bays"]
    n, m = len(B), len(bays)
    cur = cached(pid, tl, seed)
    # per placed block: layer-0 polygon and bbox at its pose
    geo = {}
    for i in range(n):
        if not cur[i*7+6]:
            continue
        sh = B[i]["shape"][cur[i*7+1]]
        polys = []
        for lay in sh["layers"]:
            pts = [(q[0]+cur[i*7+2], q[1]+cur[i*7+3]) for q in lay]
            g = Polygon(pts)
            if not g.is_valid:
                g = g.buffer(0)
            polys.append(g)
        xs = [q[0] for g in polys for q in g.exterior.coords]
        ys = [q[1] for g in polys for q in g.exterior.coords]
        geo[i] = (polys, box(min(xs), min(ys), max(xs), max(ys)))
    days = range(min(cur[i*7+4] for i in geo), max(cur[i*7+5] for i in geo)+1)
    print(f"{pid} s{seed}: {len(geo)} placed", flush=True)
    tot_pairs = tot_bbox_ol = tot_poly_ol = 0
    for j in range(m):
        W, H = bays[j]["width"], bays[j]["height"]
        peak = (0, None)
        for d in days:
            live = [i for i in geo if cur[i*7] == j
                    and cur[i*7+4] <= d < cur[i*7+5]]
            pa = sum(geo[i][0][0].area for i in live)
            ba = sum(geo[i][1].area for i in live)
            if pa > peak[0]:
                peak = (pa, d, ba, live)
        if peak[1] is None:
            continue
        pa, d, ba, live = peak
        # interlock census on the peak day: bbox-overlap vs polygon-overlap
        bb_ol = po_ol = prs = 0
        for a in range(len(live)):
            for b in range(a+1, len(live)):
                i, k = live[a], live[b]
                prs += 1
                if geo[i][1].intersection(geo[k][1]).area > 1e-6:
                    bb_ol += 1
                    L = min(len(geo[i][0]), len(geo[k][0]))
                    if any(geo[i][0][l].intersection(geo[k][0][l]).area > 1e-6
                           for l in range(L)):
                        po_ol += 1
        tot_pairs += prs; tot_bbox_ol += bb_ol; tot_poly_ol += po_ol
        print(f"   bay{j} ({W}x{H}) peak day {d}: polygon {100*pa/(W*H):.0f}%"
              f"  bbox {100*ba/(W*H):.0f}%  of bay area"
              f"  | pairs {prs}: bbox-overlap {bb_ol}, same-layer polygon"
              f"-overlap {po_ol}", flush=True)
    print(f"   TOTAL peak-day pairs {tot_pairs}: interlocked (bbox ol, polygon"
          f" disjoint) {tot_bbox_ol - tot_poly_ol}, ILLEGAL polygon overlaps"
          f" {tot_poly_ol}  <- must be 0 on a feasible board", flush=True)


if __name__ == "__main__":
    main()
