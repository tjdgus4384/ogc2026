#!/usr/bin/env python3
"""How far short is the free floor, when it is short?

`blocked.py` answers WHETHER a tardy block's layer-0 bbox fit anywhere at its
last punctual day. It does not answer BY HOW MUCH it missed, and that number
decides whether a better packing rule can ever reach these blocks:

    s ~ 0.95   the block missed by a hair -- a rule that keeps free space a
               little more contiguous would seat it, and the axis is alive;
    s ~ 0.60   the largest usable rectangle is nowhere near the block, so no
               reordering of the same placements reaches it and the axis is
               a dead end regardless of the rule.

For each block `blocked.py` classes as FRAGMENTED, binary-search the largest
uniform scale s such that a ceil(s*w) x ceil(s*h) all-free rectangle exists in
some bay at that moment (over every orientation). Occupancy is rounded OUTWARD
exactly as in blocked.py, so s is a lower bound on what really fits and the
conclusion stays sound in the direction it is used.

    python3 tools/fragmargin.py SOLVERDIR prob_5 TL [seed]
"""
import json
import math
import os
import pathlib
import statistics as st
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent


def bbox(layer):
    xs = [v[0] for v in layer]
    ys = [v[1] for v in layer]
    return min(xs), min(ys), max(xs), max(ys)


def fits(ps, W, H, w, h):
    """is there an all-free w x h axis-aligned rectangle in this grid?"""
    if w <= 0 or h <= 0 or w > W or h > H:
        return False
    for py in range(0, H - h + 1):
        a, b_ = ps[py + h], ps[py]
        for px in range(0, W - w + 1):
            if a[px + w] - a[px] - b_[px + w] + b_[px] == 0:
                return True
    return False


def main():
    solver_dir, prob_id = sys.argv[1], sys.argv[2]
    tl = float(sys.argv[3])
    seed = int(sys.argv[4]) if len(sys.argv) > 4 else 7
    sys.path.insert(0, str(REPO / solver_dir))
    os.environ["OGC_SEED"] = str(seed)
    import myalgorithm as ma
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    B, bays = prob["blocks"], prob["bays"]
    n, m = len(B), len(bays)

    sol = ma.algorithm(prob, timelimit=tl)
    eng, D, P = ma._fresh(prob, 4)
    stt = [0] * (n * 7)
    for day, ops in sol["operations"].items():
        for op in ops:
            if op["type"] == "ENTRY":
                i = op["block_id"]
                stt[i * 7:i * 7 + 7] = [op["bay_id"], op["orient_idx"], op["x"],
                                        op["y"], int(day), int(day) + P[i], 1]
    hz = eng.horizon()
    eng.set_solution(stt)
    eng.build_occ(hz)
    o = eng.objective()
    tardy = [i for i in range(n) if stt[i * 7 + 6] and stt[i * 7 + 5] > D[i]]
    print(f"{prob_id}: board {o[3]:,.0f}  Z1={o[0]:.0f}  {len(tardy)} tardy"
          f"   (TL {tl:.0f}s, seed {seed})")

    L0 = [[bbox(sh["layers"][0]) for sh in b["shape"]] for b in B]
    margins = []
    for i in tardy:
        t = D[i] - P[i]
        late = stt[i * 7 + 5] - D[i]
        if t < 0 or t > hz:
            continue
        grids = []
        for j in range(m):
            W = int(bays[j]["width"])
            H = int(bays[j]["height"])
            g = [[0] * W for _ in range(H)]
            for k in range(n):
                if not stt[k * 7 + 6] or k == i or stt[k * 7] != j:
                    continue
                if not (stt[k * 7 + 4] <= t < stt[k * 7 + 5]):
                    continue
                x0, y0, x1, y1 = L0[k][stt[k * 7 + 1]]
                cx0 = max(0, int(math.floor(stt[k * 7 + 2] + x0)))
                cy0 = max(0, int(math.floor(stt[k * 7 + 3] + y0)))
                cx1 = min(W, int(math.ceil(stt[k * 7 + 2] + x1)))
                cy1 = min(H, int(math.ceil(stt[k * 7 + 3] + y1)))
                for yy in range(cy0, cy1):
                    row = g[yy]
                    for xx in range(cx0, cx1):
                        row[xx] = 1
            ps = [[0] * (W + 1) for _ in range(H + 1)]
            for yy in range(H):
                acc = 0
                for xx in range(W):
                    acc += g[yy][xx]
                    ps[yy + 1][xx + 1] = ps[yy][xx + 1] + acc
            grids.append((ps, W, H))

        def ok(s):
            for oi in range(len(B[i]["shape"])):
                ax0, ay0, ax1, ay1 = L0[i][oi]
                w = int(math.ceil((ax1 - ax0) * s))
                h = int(math.ceil((ay1 - ay0) * s))
                for ps, W, H in grids:
                    if fits(ps, W, H, w, h):
                        return True
            return False

        if ok(1.0):
            continue                      # shadow case: blocked.py's other bin
        lo, hi = 0.0, 1.0
        for _ in range(7):                # 1/128 resolution is plenty
            mid = (lo + hi) / 2
            if ok(mid):
                lo = mid
            else:
                hi = mid
        margins.append((lo, late, i))

    if not margins:
        print("   no fragmented blocks on this board")
        return
    vals = sorted(x[0] for x in margins)
    print(f"   {len(margins)} FRAGMENTED blocks -- largest uniform scale that fits:")
    print(f"      min {vals[0]:.2f}   q1 {vals[len(vals)//4]:.2f}   "
          f"median {st.median(vals):.2f}   q3 {vals[3*len(vals)//4]:.2f}   "
          f"max {vals[-1]:.2f}")
    for lab, lo_, hi_ in (("s >= 0.90 (아슬아슬)", 0.90, 1.01),
                          ("0.75 <= s < 0.90", 0.75, 0.90),
                          ("0.50 <= s < 0.75", 0.50, 0.75),
                          ("s < 0.50 (구조적)", -1.0, 0.50)):
        sel = [x for x in margins if lo_ <= x[0] < hi_]
        print(f"      {lab:<24s} {len(sel):3d} blocks, {sum(x[1] for x in sel):5d} days")


if __name__ == "__main__":
    main()
