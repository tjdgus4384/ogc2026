#!/usr/bin/env python3
"""What does a board BELOW the plateau look like?

The campaign has never been able to ask this: every diagnostic compared our
board against itself because no better solution existed. `p5_b40_y1` plus a
60x-budget run gives one -- same instance, roughly half the tardiness -- and
the instance has a single bay, so assignment drops out and the difference lives
entirely in entry days and positions.

Runs the same seed at two budgets, then compares the two boards:

  entry days     how many blocks move in time, and by how much
  positions      how many move in space at an unchanged entry day
  tardy set      is the better board tardy on the SAME blocks, or different ones
  occupancy      does the better basin pack tighter, or just schedule better

The last line is the one that matters. If the better board's peak occupancy is
the same, the win is scheduling and the packing was never the binding thing; if
it packs tighter, the geometry is where the gap lives and every packing lever we
closed today needs re-reading.

    python3 tools/basin.py TL_LOW TL_HIGH [seed]
"""
import json
import os
import pathlib
import statistics as st
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent


def run(prob, tl, seed):
    # OGC_SEED must be set BEFORE myalgorithm is imported -- _PSEED is read at
    # import time, so setting it here silently did nothing and both "seeds"
    # were the same run (caught 08-13 when two seeds returned byte-identical
    # boards). The caller sets it; this asserts the caller got it right.
    import myalgorithm as ma
    assert ma._PSEED == seed, f"seed not in effect: _PSEED={ma._PSEED}"
    sol = ma.algorithm(prob, timelimit=tl)
    n = len(prob["blocks"])
    P = [max(1, int(-(-b["processing_time"] // 1))) for b in prob["blocks"]]
    st_ = [None] * n
    for day, ops in sol["operations"].items():
        for op in ops:
            if op["type"] == "ENTRY":
                i = op["block_id"]
                st_[i] = (op["bay_id"], op["orient_idx"], op["x"], op["y"],
                          int(day), int(day) + P[i])
    return st_, ma._check(prob, sol)


def occupancy(prob, board):
    """polygon area resident per day / total bay area"""
    def parea(vs):
        a = 0.0
        for i in range(len(vs)):
            x1, y1 = vs[i]
            x2, y2 = vs[(i + 1) % len(vs)]
            a += x1 * y2 - x2 * y1
        return abs(a) / 2
    cap = sum(b["width"] * b["height"] for b in prob["bays"])
    T = max(s[5] for s in board if s) + 1
    day = [0.0] * (T + 1)
    for i, s in enumerate(board):
        if not s:
            continue
        L = prob["blocks"][i]["shape"][s[1]]["layers"][0]
        a = parea(L)
        for t in range(s[4], min(s[5], T)):
            day[t] += a
    return max(day) / cap, st.mean([d for d in day if d > 0]) / cap


def main():
    tl_lo = float(sys.argv[1])
    tl_hi = float(sys.argv[2])
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 7
    sys.path.insert(0, str(REPO / "solver"))
    import myalgorithm as ma
    prob = json.load(open(REPO / "data" / "mini" / "p5_b40_y1.json"))
    n = len(prob["blocks"])
    D = [int(round(b["due_date"])) for b in prob["blocks"]]

    lo, rlo = run(prob, tl_lo, seed)
    hi, rhi = run(prob, tl_hi, seed)
    print(f"p5_b40_y1 seed {seed}:  TL{tl_lo:.0f} {rlo['objective']:,.0f}"
          f"   ->   TL{tl_hi:.0f} {rhi['objective']:,.0f}")
    if rhi["objective"] >= rlo["objective"]:
        print("   the long run did not win on this seed -- nothing to compare")
        return

    moved_t = [i for i in range(n) if lo[i] and hi[i] and lo[i][4] != hi[i][4]]
    moved_x = [i for i in range(n) if lo[i] and hi[i] and lo[i][4] == hi[i][4]
               and (lo[i][2], lo[i][3], lo[i][1]) != (hi[i][2], hi[i][3], hi[i][1])]
    same = n - len(moved_t) - len(moved_x)
    tlo = {i for i in range(n) if lo[i] and lo[i][5] > D[i]}
    thi = {i for i in range(n) if hi[i] and hi[i][5] > D[i]}
    print(f"   entry day moved   {len(moved_t):3d}/{n}"
          + (f"   median shift {st.median([hi[i][4]-lo[i][4] for i in moved_t]):+.0f}d"
             if moved_t else ""))
    print(f"   position only     {len(moved_x):3d}/{n}")
    print(f"   identical         {same:3d}/{n}")
    print(f"   tardy set         TL{tl_lo:.0f} {sorted(tlo)}")
    print(f"                     TL{tl_hi:.0f} {sorted(thi)}")
    print(f"                     shared {len(tlo & thi)}, only-low {len(tlo - thi)},"
          f" only-high {len(thi - tlo)}")
    plo, alo = occupancy(prob, lo)
    phi, ahi = occupancy(prob, hi)
    print(f"   occupancy peak    {plo:.3f}  ->  {phi:.3f}")
    print(f"   occupancy mean    {alo:.3f}  ->  {ahi:.3f}")
    print("   => " + ("packs tighter -- geometry is where the gap lives"
                      if phi > plo + 0.01 else
                      "same density -- the win is SCHEDULING, not packing"))


if __name__ == "__main__":
    main()
