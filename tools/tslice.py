#!/usr/bin/env python3
"""Is the neighbourhood's SHAPE the untested dimension, not its size?

The elimination swept neighbourhood SIZE: terminal.py destroyed 5/15/30/60%
of the shipped board 400 times and exactrepair.py solved the same holes to
optimality with CP-SAT -- 0 improvements either way. But those destroy sets
are random or hole-based, and the kernel's own structured operator (kind 5)
slices ONE bay. A window across ALL bays has never been tried.

That shape is the natural neighbourhood for a scheduling problem: everything
resident during [t, t+W) comes out together, so the re-insertion can re-order
who occupies the congested days rather than shuffling one bay's tenants. If the
board is a fixed point of this too, the neighbourhood axis is closed by shape as
well as by size, and the remaining gap is not reachable by re-solving parts of
our own solution at all.

Deterministic on the shipped board -- no gate, no lottery. Reinsertion uses the
kernel's own greedy in due-date order, the same rule that built the board, so
any improvement is attributable to the window, not to a different repair.

    python3 tools/tslice.py prob_5 TL [seed] [widths...]
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
    widths = [int(x) for x in sys.argv[4:]] or [5, 10, 20, 40]
    sys.path.insert(0, str(REPO / "solver"))
    os.environ["OGC_SEED"] = str(seed)
    import myalgorithm as ma
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    n = len(prob["blocks"])

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
    o0 = eng.objective()
    T = max(base[i * 7 + 5] for i in range(n) if base[i * 7 + 6])
    print(f"{prob_id}: shipped {o0[3]:,.0f}  Z1={o0[0]:.0f}"
          f"   (TL {tl:.0f}s, seed {seed}, horizon used {T})")

    for W in widths:
        best = 0.0
        tries = hits = 0
        biggest = None
        for t0 in range(0, max(1, T - W + 1)):
            cut = [i for i in range(n)
                   if base[i * 7 + 6]
                   and base[i * 7 + 4] < t0 + W and base[i * 7 + 5] > t0]
            if not cut or len(cut) > n // 2:
                continue
            tries += 1
            st = list(base)
            for i in cut:
                st[i * 7 + 6] = 0
            eng.set_solution(st)
            eng.build_occ(hz)
            # the board's own rule: earliest due first, longest first on ties
            for i in sorted(cut, key=lambda i: (D[i], -P[i])):
                eng.insert_capped(i, -1, -1)
            o = eng.objective()
            if all(eng.get_block(i)[6] for i in cut):
                d = (o[3] - o0[3]) / o0[3] * 100
                if d < -1e-9:
                    hits += 1
                    if d < best:
                        best = d
                        biggest = (t0, len(cut))
        eng.set_solution(base)
        eng.build_occ(hz)
        tag = (f"best {best:+.2f}% at t={biggest[0]} ({biggest[1]} blocks)"
               if biggest else "no window improves")
        print(f"   W={W:<3d} {tries:3d} windows tried, {hits:3d} improved   {tag}")


if __name__ == "__main__":
    main()
