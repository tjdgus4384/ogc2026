#!/usr/bin/env python3
"""Force the decisive moves and let a full reconstruction do the accommodation.

The problem, in the form today's measurements left it: a better board differs
from ours by 3-5 blocks' timing plus 34-35 repositions that merely accommodate
those few, at identical density. Small neighbourhoods cannot see the decisive
few (0/400 local, 0/20 exact CP, 0/3,680 windows); only a full restart reaches
them, because replacing every block makes accommodation automatic.

So bias the restart. Take the tardy blocks -- the ones whose timing must change
-- force their entry to their punctual limit with the kernel's entry lock, and
reconstruct EVERYTHING. The forced blocks supply the decisive moves; the other
35 accommodate by construction. That is the one combination nothing has tried:
every previous experiment either moved a few blocks (no accommodation) or
reconstructed without direction (no decisive move).

Deterministic: reconstruct_ordered has a fixed internal seed, so a given
(lock, order) always gives the same board.

    python3 tools/force.py prob_5 TL [seed]
"""
import json
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent


def main():
    prob_id = sys.argv[1]
    tl = float(sys.argv[2])
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 7
    sys.path.insert(0, str(REPO / "tools"))
    from board import get_board
    base, D, P, eng, hz, prob = get_board(prob_id, tl, seed)
    n = len(prob["blocks"])
    o0 = eng.objective()
    tardy = [i for i in range(n) if base[i * 7 + 6] and base[i * 7 + 5] > D[i]]
    print(f"{prob_id}: shipped {o0[3]:,.0f}  Z1={o0[0]:.0f}  {len(tardy)} tardy"
          f"   (TL {tl:.0f}s, seed {seed})")

    orders = {
        "due":      sorted(range(n), key=lambda i: (D[i], -P[i])),
        "slack":    sorted(range(n), key=lambda i: (D[i] - P[i] - int(
                        prob["blocks"][i]["release_time"]), D[i])),
        "long":     sorted(range(n), key=lambda i: (-P[i], D[i])),
    }
    # BASELINE FIX: the shipped board is annealed and a raw reconstruction is
    # not, so comparing them measured the missing anneal (+171% on P4), not the
    # forcing. Each order's own force-0 reconstruction is the baseline.
    best = (None, None, 0)
    ref = {}
    for oname, order in orders.items():
        for k in (0, 1, 2, 3, 5, 8, 12, len(tardy)):
            if k > len(tardy):
                continue
            # force the k worst-late blocks to enter by their punctual day
            worst = sorted(tardy, key=lambda i: -(base[i * 7 + 5] - D[i]))[:k]
            cap = [-1] * n
            for i in worst:
                cap[i] = max(0, D[i] - P[i])
            # reconstruct_ordered builds its OWN Ctx and never copies
            # entryCap (api.cpp:504 propagates it to the anneal threads only),
            # so set_entry_lock is silently ignored there -- the first version
            # of this probe forced nothing and every force level returned an
            # identical board. Reconstruct by hand with insert_capped, which
            # takes the cap per call.
            eng.set_solution([0] * (n * 7))
            eng.build_occ(hz)
            ok = True
            for i in order:
                if not eng.insert_capped(i, cap[i], -1):
                    ok = False
                    break
            if not ok:
                continue
            o = eng.objective()
            if k == 0:
                ref[oname] = o[3]
            d = (o[3] - ref[oname]) / ref[oname] * 100
            mark = "  <-- beats its own force-0" if d < -1e-9 else ""
            if best[0] is None or d < best[0]:
                best = (d, oname, k)
            print(f"   {oname:<6s} force {k:2d}: {o[3]:>14,.0f}  {d:+7.2f}%"
                  f"  Z1={o[0]:.0f}{mark}")
    eng.set_solution(base)
    eng.build_occ(hz)
    print("   => " + (f"best {best[0]:+.2f}% ({best[1]}, force {best[2]})"
                      if best[0] is not None and best[0] < -1e-9 else
                      "forcing never beats the unforced reconstruction"))


if __name__ == "__main__":
    main()
