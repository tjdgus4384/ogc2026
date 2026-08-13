#!/usr/bin/env python3
"""How many residents must be lifted out before a tardy block fits? (vp-exact)

`blocked.py` and `fragmargin.py` are bbox-on-bbox: resident occupancy is their
bounding boxes and the arriving block is its bounding box. Both overstate, so
their NEGATIVE answers are not sound -- and the layer-0 polygon/bbox area ratio
is only 0.59-0.71, i.e. exactly the scale those tools reported as "missing".
Their cause split and margin numbers are retracted (ledger 08-13).

This asks the same question with no approximation at all. Restore the shipped
board, unplace the tardy block, and try the KERNEL's own punctual insertion
(`insert_capped` with eCap = D-P, which runs the real vp check over the real
polygons). It fails -- that is why the block is tardy. Then lift residents out
one at a time and retry, counting how many must go:

    k = 1     one neighbour is in the way; a placement rule that moved that one
              block elsewhere would have seated this one, and the axis is alive;
    k >= 4    the block needs a large coordinated rearrangement, which is the
              same statement as "the basin is wrong", not "the rule is wrong".

Residents are lifted in decreasing time-overlap with the block's stay, longest
first, which is the greedy most likely to free the window in fewest lifts -- so
k is an UPPER bound on the true minimum, and a small k is therefore sound
evidence that the block was close.

    python3 tools/liftk.py SOLVERDIR prob_5 TL [seed] [kmax]
"""
import json
import os
import pathlib
import statistics as st
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent


def main():
    solver_dir, prob_id = sys.argv[1], sys.argv[2]
    tl = float(sys.argv[3])
    seed = int(sys.argv[4]) if len(sys.argv) > 4 else 7
    kmax = int(sys.argv[5]) if len(sys.argv) > 5 else 6
    sys.path.insert(0, str(REPO / solver_dir))
    os.environ["OGC_SEED"] = str(seed)
    import myalgorithm as ma
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    B = prob["blocks"]
    n = len(B)

    sol = ma.algorithm(prob, timelimit=tl)
    eng, D, P = ma._fresh(prob, 4)
    base = [0] * (n * 7)
    for day, ops in sol["operations"].items():
        for op in ops:
            if op["type"] == "ENTRY":
                i = op["block_id"]
                base[i * 7:i * 7 + 7] = [op["bay_id"], op["orient_idx"],
                                         op["x"], op["y"], int(day),
                                         int(day) + P[i], 1]
    hz = eng.horizon()
    eng.set_solution(base)
    eng.build_occ(hz)
    o = eng.objective()
    tardy = [i for i in range(n) if base[i * 7 + 6] and base[i * 7 + 5] > D[i]]
    print(f"{prob_id}: board {o[3]:,.0f}  Z1={o[0]:.0f}  {len(tardy)} tardy"
          f"   (TL {tl:.0f}s, seed {seed}, kmax {kmax})")

    def restore(state):
        eng.set_solution(state)
        eng.build_occ(hz)

    res = []
    for i in tardy:
        cap = D[i] - P[i]
        if cap < 0:
            res.append((None, base[i * 7 + 5] - D[i]))   # impossible by release
            continue
        st0 = list(base)
        st0[i * 7 + 6] = 0                                # lift the block itself
        # residents ordered by overlap with the block's punctual stay
        lo, hi = cap, cap + P[i]
        cand = []
        for k in range(n):
            if k == i or not st0[k * 7 + 6]:
                continue
            ov = min(hi, st0[k * 7 + 5]) - max(lo, st0[k * 7 + 4])
            if ov > 0:
                cand.append((ov, k))
        cand.sort(reverse=True)
        lifted = []
        got = None
        for kk in range(0, kmax + 1):
            trial = list(st0)
            for _, b in lifted:
                trial[b * 7 + 6] = 0
            restore(trial)
            if eng.insert_capped(i, cap, -1):
                got = kk
                break
            if kk == kmax or kk >= len(cand):
                break
            lifted.append(cand[kk])
        res.append((got, base[i * 7 + 5] - D[i]))

    restore(base)
    ks = [r[0] for r in res if r[0] is not None]
    print(f"   lifts needed to seat a tardy block punctually "
          f"({len(ks)}/{len(res)} resolved within kmax):")
    for kk in range(0, kmax + 1):
        sel = [r for r in res if r[0] == kk]
        if sel:
            print(f"      k = {kk:<2d}  {len(sel):3d} blocks, {sum(r[1] for r in sel):5d} tardy days")
    un = [r for r in res if r[0] is None]
    if un:
        print(f"      k > {kmax}   {len(un):3d} blocks, {sum(r[1] for r in un):5d} tardy days")
    if ks:
        print(f"   median k = {st.median(ks):.1f}")


if __name__ == "__main__":
    main()
