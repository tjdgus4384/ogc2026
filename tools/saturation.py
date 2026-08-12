#!/usr/bin/env python3
"""WHY is there no legal position? Area, or fragmentation?

Every probe so far asked "can the board improve from here", twenty different
ways, and every one returned zero. That is the wrong question: each of them
freezes most of the board, and the frozen part is what does the blocking, so
they can only ever re-confirm local optimality -- which they did.

Two instance-level facts reframe it:

  * Z1_floor = 0 on all four boards. Every block satisfies R+P <= D, so NOT ONE
    day of our tardiness is forced by an individual block's own dates. P4 174
    days, P5 701, P6 4072 are all self-inflicted at the board level.
  * The area that MUST be resident on a given day under ANY punctual schedule
    (block i is forced resident on [D-P, R+P)) peaks at 32.6% of the yard on
    P3, 60.5% on P4, 92.9% on P5, 97.4% on P6 -- exact polygon area, no packing
    waste at all.

So P5/P6 are near enough to area-saturated that punctuality is out of reach
(the winning entries carry 539 and 3670 tardy days themselves), while P4 has
real room and is where we trail the winner worst -- 174 against 95, 1.83x, the
largest objective gap of the four.

missed.py already proved that when a punctual insert fails there is genuinely
no legal position. This asks why, and the answer picks the lever:

  bay is area-full at that day    the failure is forced; only a better GLOBAL
                                  decision about who yields can help;
  bay has area to spare           the failure is fragmentation or crane
                                  shadow, and better PACKING closes it.

For every tardy block we look at the last day it could still have been
punctual, and compare its own footprint against the residual area of each bay.

    python3 tools/saturation.py SOLVERDIR prob_4 TL [seed]
"""
import json
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent


def poly_area(v):
    s = 0.0
    for k in range(len(v)):
        x1, y1 = v[k]
        x2, y2 = v[(k + 1) % len(v)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2


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
    bay_area = [b["width"] * b["height"] for b in bays]
    cap = sum(bay_area)
    # footprint per (block, orientation): the crane sterilises the union of the
    # layers, so the honest occupancy is the union area, not layer 0's.
    AREA = []
    for b in B:
        per = []
        for sh in b["shape"]:
            per.append(max(poly_area(l) for l in sh["layers"]))
        AREA.append(per)

    sol = ma.algorithm(prob, timelimit=tl)
    eng, D, P = ma._fresh(prob, 4)
    st = [0] * (n * 7)
    for day, ops in sol["operations"].items():
        for op in ops:
            i = op["block_id"]
            if op["type"] == "ENTRY":
                st[i * 7:i * 7 + 7] = [op["bay_id"], op["orient_idx"], op["x"],
                                       op["y"], int(day), int(day) + P[i], 1]
    hz = eng.horizon()
    eng.set_solution(st)
    eng.build_occ(hz)
    o = eng.objective()
    print(f"{prob_id}: board {o[3]:,.0f}   Z1={o[0]:.0f} Z2={o[1]:.0f} Z3={o[2]:.0f}"
          f"   (TL {tl:.0f}s, seed {seed}, yard {cap:,} over {m} bays)")

    # ---- occupancy profile of OUR board, per day per bay
    occ = [[0.0] * m for _ in range(hz + 1)]
    for i in range(n):
        if not st[i * 7 + 6]:
            continue
        j, oi, e, f = st[i * 7], st[i * 7 + 1], st[i * 7 + 4], st[i * 7 + 5]
        a = AREA[i][oi]
        for t in range(e, min(hz + 1, f)):
            occ[t][j] += a
    tot = [sum(r) for r in occ]
    pk = max(range(hz + 1), key=lambda t: tot[t])
    print(f"   our peak occupancy: day {pk}, {tot[pk]:,.0f} of {cap:,} "
          f"({tot[pk]/cap*100:.1f}%)   per bay "
          f"{['%.0f%%' % (occ[pk][j]/bay_area[j]*100) for j in range(m)]}")

    # ---- the tardy blocks, and what the yard looked like when they lost
    tardy = [i for i in range(n) if st[i * 7 + 6] and st[i * 7 + 5] > D[i]]
    print(f"   {len(tardy)} tardy blocks carry {o[0]:.0f} days"
          f"   (median {sorted(st[i*7+5]-D[i] for i in tardy)[len(tardy)//2] if tardy else 0} d)")
    forced = frag = 0
    fdays = gdays = 0
    for i in tardy:
        oi = st[i * 7 + 1]
        a = AREA[i][oi]
        t = D[i] - P[i]                     # last day it could still be punctual
        late = st[i * 7 + 5] - D[i]
        if t < 0 or t > hz:
            continue
        free = [bay_area[j] - occ[t][j] for j in range(m)]
        # its own share is not in occ[t] (it entered later), so `free` is honest
        if max(free) < a:
            forced += 1
            fdays += late
        else:
            frag += 1
            gdays += late
    print(f"   at the last punctual day, per tardy block:")
    print(f"      no bay had room at all      {forced:4d} blocks, {fdays:5d} tardy days")
    print(f"      a bay had MORE free area    {frag:4d} blocks, {gdays:5d} tardy days"
          f"   <- fragmentation / crane shadow, not area")
    # how much slack did the roomy cases have?
    if frag:
        rat = []
        for i in tardy:
            oi = st[i * 7 + 1]
            a = AREA[i][oi]
            t = D[i] - P[i]
            if t < 0 or t > hz:
                continue
            free = max(bay_area[j] - occ[t][j] for j in range(m))
            if free >= a:
                rat.append(free / a)
        rat.sort()
        print(f"      free/needed for those:  min {rat[0]:.1f}x  "
              f"median {rat[len(rat)//2]:.1f}x  max {rat[-1]:.1f}x")


if __name__ == "__main__":
    main()
