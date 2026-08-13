#!/usr/bin/env python3
"""What blocks the tardy blocks? A per-block eviction census on a cached board.

z1bound says area forces ZERO tardy days on P4, yet the shipped board leaves 26
blocks late. This asks, for each of them, the only question that distinguishes
"no room" from "search never found the room":

  1  frozen  -- with the whole rest of the board frozen, can the block enter at
     its punctual entry (D-P) at all? and what is the earliest entry it CAN
     reach in each bay?
  2  cleared -- clear every resident of bay j whose stay overlaps the block's
     punctual window, seat the block punctually, then put the cleared residents
     back at their EXACT original placements. Whoever cannot go back is the
     eviction set: the blocks that must physically move for this one to be
     punctual. Greedy re-add order (largest bbox first), so the reported size
     is an upper bound on the true minimum cut.

    python3 tools/p4block.py [prob_4] [480] [7]
"""
import json
import math
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
from board import get_board                                # noqa: E402


def main():
    pid = sys.argv[1] if len(sys.argv) > 1 else "prob_4"
    tl = float(sys.argv[2]) if len(sys.argv) > 2 else 480.0
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 7
    base, D, P, eng, hz, prob = get_board(pid, tl, seed)
    B = prob["blocks"]
    n = len(B)
    m = len(prob["bays"])
    R = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]
    PREF = [b["bay_preferences"] for b in B]
    rec = {i: base[i * 7:i * 7 + 7] for i in range(n)}

    def bbox(i, oi):
        vs = [v for lay in B[i]["shape"][oi]["layers"] for v in lay]
        xs = [v[0] for v in vs]
        ys = [v[1] for v in vs]
        return (max(xs) - min(xs)) * (max(ys) - min(ys))

    tardy = [i for i in range(n) if rec[i][6] and rec[i][5] > D[i]]
    z1 = sum(rec[i][5] - D[i] for i in tardy)
    print(f"# {pid} TL{tl:.0f} s{seed}  n={n} m={m}  tardy={len(tardy)} "
          f"z1={z1} days  obj={eng.objective()}")

    print(f"\n{'id':>4}{'late':>5}{'e':>4}{'pun':>4}{'R':>4}  "
          f"{'frozen@pun':>11}  earliest-entry-per-bay(frozen)   "
          f"min-eviction-set")
    tot_ev = []
    for i in sorted(tardy, key=lambda i: -(rec[i][5] - D[i])):
        punct = D[i] - P[i]
        solo = list(base)
        solo[i * 7 + 6] = 0
        eng.set_solution(solo)
        eng.build_occ(hz)
        frozen_ok = eng.insert_capped(i, punct, -1)
        eng.set_solution(solo)
        eng.build_occ(hz)
        fp = eng.feasible_positions(i, 200, 0)
        per = {}
        for q in range(0, len(fp), 5):
            j, oi, x, y, e = fp[q:q + 5]
            if j not in per or e < per[j]:
                per[j] = e
        best = None
        for j in range(m):
            fs = punct + P[i]
            win = [k for k in range(n) if rec[k][6] and k != i
                   and rec[k][0] == j and rec[k][4] < fs and punct < rec[k][5]]
            flat = list(base)
            flat[i * 7 + 6] = 0
            for k in win:
                flat[k * 7 + 6] = 0
            eng.set_solution(flat)
            eng.build_occ(hz)
            if not eng.insert_capped(i, punct, j):
                continue                       # no room even with j cleared
            bad = []
            for k in sorted(win, key=lambda k: -bbox(k, rec[k][1])):
                r = rec[k]
                if not eng.place_at(k, r[0], r[1], r[2], r[3], r[4]):
                    bad.append(k)
            if best is None or len(bad) < len(best[1]):
                best = (j, bad, len(win))
        tot_ev.append(0 if best is None else len(best[1]))
        ev = ("none-fits" if best is None
              else f"bay{best[0]}: {len(best[1])} of {best[2]} "
                   f"{best[1][:8]}")
        print(f"{i:>4}{rec[i][5] - D[i]:>5}{rec[i][4]:>4}{punct:>4}{R[i]:>4}  "
              f"{str(frozen_ok):>11}  "
              + " ".join(f"b{j}:{per.get(j, '-'):>3}" for j in range(m))
              + f"   {ev}")
    fit = [v for v in tot_ev if v is not None]
    print(f"\neviction-set sizes: {sorted(tot_ev)}")
    print(f"mean {sum(fit) / max(1, len(fit)):.1f}  max {max(fit)}  "
          f"sum {sum(fit)}")

    # cohort: free every tardy block at once, re-seat all of them punctually
    eng.set_solution(base)
    eng.build_occ(hz)
    flat = list(base)
    for i in tardy:
        flat[i * 7 + 6] = 0
    eng.set_solution(flat)
    eng.build_occ(hz)
    order = sorted(tardy, key=lambda i: -bbox(i, rec[i][1]) * P[i])
    ok = [i for i in order if eng.insert_capped(i, D[i] - P[i], -1)]
    print(f"\ncohort (all {len(tardy)} freed, re-seated hardest-first with a "
          f"punctual cap): {len(ok)} of {len(tardy)} seated punctually")
    left = [i for i in order if i not in set(ok)]
    print(f"  unseated: {left}")
    for i in left:
        print(f"    block {i}: R={R[i]} D={D[i]} P={P[i]} "
              f"punct={D[i] - P[i]} bbox={bbox(i, rec[i][1]):.0f} "
              f"pref={PREF[i]}")


if __name__ == "__main__":
    main()
