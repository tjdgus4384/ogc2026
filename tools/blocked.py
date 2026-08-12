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
        found = False
        for j in range(m):
            W, H = bays[j]["width"], bays[j]["height"]
            occ = []
            for k in range(n):
                if not st[k * 7 + 6] or k == i or st[k * 7] != j:
                    continue
                if not (st[k * 7 + 4] <= t < st[k * 7 + 5]):
                    continue
                x0, y0, x1, y1 = L0[k][st[k * 7 + 1]]
                occ.append((st[k * 7 + 2] + x0, st[k * 7 + 3] + y0,
                            st[k * 7 + 2] + x1, st[k * 7 + 3] + y1))
            for oi in range(len(B[i]["shape"])):
                ax0, ay0, ax1, ay1 = L0[i][oi]
                w, h = ax1 - ax0, ay1 - ay0
                for px in range(0, int(W - w) + 1):
                    for py in range(0, int(H - h) + 1):
                        qx0, qy0 = px, py
                        qx1, qy1 = px + w, py + h
                        if any(not (qx1 <= r[0] + 1e-9 or r[2] <= qx0 + 1e-9
                                    or qy1 <= r[1] + 1e-9 or r[3] <= qy0 + 1e-9)
                               for r in occ):
                            continue
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
