#!/usr/bin/env python3
"""Fragmentation, or crane shadow? The last unknown in the Z1 chain.

    Z1 is 78-97% of the objective on P4/P5/P6
      -> a block is tardy IFF its punctual insertion failed
        -> at that moment there really was no legal position  (missed.py, 59/60)
          -> yet 4.5-24.7x its own footprint was free         (saturation.py)
            -> WHY?

Two mechanisms are left and they call for opposite solvers:

  fragmentation   the free floor is scattered into pieces smaller than the
                  block. The lever is a packing policy that keeps free space
                  contiguous;
  crane shadow    the floor is free and CONTIGUOUS, but a resident's upper
                  layers overhang it, so the descent is blocked. The lever is
                  to place by what a block sterilises, not by what it covers.

Split them by asking a strictly 2D question at layer 0. Occupancy is taken as
the union of residents' layer-0 BOUNDING BOXES, which over-states what they
really cover -- so a position found here is certainly a position a polygon
would fit in, and a positive answer is sound. If such a position exists while
the kernel's own `vp_ok` rejects everything, the floor was never the problem
and the shadow is.

    python3 tools/blocked.py SOLVERDIR prob_5 TL [seed]
"""
import json
import math
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent


def bbox(layer):
    xs = [v[0] for v in layer]
    ys = [v[1] for v in layer]
    return min(xs), min(ys), max(xs), max(ys)


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
    st = [0] * (n * 7)
    for day, ops in sol["operations"].items():
        for op in ops:
            if op["type"] == "ENTRY":
                i = op["block_id"]
                st[i * 7:i * 7 + 7] = [op["bay_id"], op["orient_idx"], op["x"],
                                       op["y"], int(day), int(day) + P[i], 1]
    hz = eng.horizon()
    eng.set_solution(st)
    eng.build_occ(hz)
    o = eng.objective()
    tardy = [i for i in range(n) if st[i * 7 + 6] and st[i * 7 + 5] > D[i]]
    print(f"{prob_id}: board {o[3]:,.0f}  Z1={o[0]:.0f}  {len(tardy)} tardy blocks"
          f"   (TL {tl:.0f}s, seed {seed})")

    # layer-0 bbox of every (block, orientation), and the LAYER-0 extent only:
    # the union over layers is what the crane sterilises, and separating the
    # two is the entire point of this probe.
    L0 = []
    for b in B:
        L0.append([bbox(sh["layers"][0]) for sh in b["shape"]])

    frag = shadow = 0
    fdays = sdays = 0
    for i in tardy:
        t = D[i] - P[i]                       # last day it could be punctual
        late = st[i * 7 + 5] - D[i]
        if t < 0 or t > hz:
            continue
        # Rasterise each bay once and take 2D prefix sums, so a candidate
        # position costs O(1) instead of a scan over residents. Occupancy is
        # rounded OUTWARD and the arriving block's bbox is rounded outward too,
        # so both sides are over-stated and a slot found here is one a real
        # polygon would certainly fit -- which is the direction the conclusion
        # needs.
        found = False
        for j in range(m):
            W, H = int(bays[j]["width"]), int(bays[j]["height"])
            g = [[0] * (W + 1) for _ in range(H + 1)]
            for k in range(n):
                if not st[k * 7 + 6] or k == i or st[k * 7] != j:
                    continue
                if not (st[k * 7 + 4] <= t < st[k * 7 + 5]):
                    continue
                x0, y0, x1, y1 = L0[k][st[k * 7 + 1]]
                cx0 = max(0, int(math.floor(st[k * 7 + 2] + x0)))
                cy0 = max(0, int(math.floor(st[k * 7 + 3] + y0)))
                cx1 = min(W, int(math.ceil(st[k * 7 + 2] + x1)))
                cy1 = min(H, int(math.ceil(st[k * 7 + 3] + y1)))
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
            for oi in range(len(B[i]["shape"])):
                ax0, ay0, ax1, ay1 = L0[i][oi]
                w = int(math.ceil(ax1 - ax0))
                h = int(math.ceil(ay1 - ay0))
                if w > W or h > H:
                    continue
                for py in range(0, H - h + 1):
                    a, b_ = ps[py + h], ps[py]
                    for px in range(0, W - w + 1):
                        if a[px + w] - a[px] - b_[px + w] + b_[px] == 0:
                            found = True
                            break
                    if found:
                        break
                if found:
                    break
            if found:
                break
        if found:
            shadow += 1
            sdays += late
        else:
            frag += 1
            fdays += late
    print(f"   at the last punctual day, per tardy block:")
    print(f"      a free layer-0 BBOX slot existed  {shadow:4d} blocks, {sdays:5d} days"
          f"   <- floor was fine; the CRANE SHADOW blocked it")
    print(f"      no free bbox slot anywhere        {frag:4d} blocks, {fdays:5d} days"
          f"   <- FRAGMENTED floor")


if __name__ == "__main__":
    main()
