#!/usr/bin/env python3
"""Is the INSERTION ORDER worth searching?

Everything measured on 2026-08-12 says a finished board is rigid: no tardy block
has an earlier legal entry, no preference relocation is feasible, and brute
force finds no legal position at all for the worst offenders on any day they
waited. If nothing moves once the board exists, the board is decided by the
order blocks were inserted in -- and the solver only ever tries six fixed
orders, keeping the best three distinct layouts.

Past refutations on this axis (reverse-due, sigma-ladder, (D,-area)) all
replaced one fixed order with another fixed order. None searched the space.
This probe does: start from the engine's own best order and hill-climb on
segment reversals and block displacements, rebuilding greedily each time and
scoring with the true objective.

    python3 tools/order_search.py SOLVERDIR prob_6 SECONDS [seed]

Reports the best objective found against the starting one, and how many
rebuilds fit in the budget -- which decides whether this can live inside a
real time limit.
"""
import json
import pathlib
import random
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent


def main():
    solver_dir, prob_id = sys.argv[1], sys.argv[2]
    budget = float(sys.argv[3])
    seed = int(sys.argv[4]) if len(sys.argv) > 4 else 7
    sys.path.insert(0, str(REPO / solver_dir))
    import myalgorithm as ma
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    B = prob["blocks"]
    n = len(B)
    eng, D, P = ma._fresh(prob)
    free = [-1] * n
    R = [int(b["release_time"]) for b in B]

    def score(order):
        if eng.reconstruct_ordered(free, order, False, 7) != -1:
            return None
        return eng.objective()[3]

    rng = random.Random(seed)
    # the driver's own first choice: due date, long processing first
    cur = sorted(range(n), key=lambda i: (D[i], -P[i]))
    best = score(cur)
    start = best
    if best is None:
        print(f"{prob_id}: base order does not construct")
        return
    t0 = time.time()
    rebuilds = 1
    accepted = 0
    while time.time() - t0 < budget:
        cand = list(cur)
        if rng.random() < 0.5:                     # reverse a short segment
            a = rng.randrange(n)
            b = min(n, a + 2 + rng.randrange(12))
            cand[a:b] = reversed(cand[a:b])
        else:                                      # displace one block
            a = rng.randrange(n)
            blk = cand.pop(a)
            cand.insert(max(0, min(n - 1, a + rng.randint(-20, 20))), blk)
        ob = score(cand)
        rebuilds += 1
        if ob is not None and ob < best - 1e-9:
            best, cur = ob, cand
            accepted += 1
    el = time.time() - t0
    print(f"{solver_dir} {prob_id} seed {seed}: {rebuilds} rebuilds in {el:.0f}s "
          f"({rebuilds / el:.1f}/s)")
    print(f"   start {start:,.0f} -> best {best:,.0f}  "
          f"({(best - start) / start * 100:+.2f}%, {accepted} improving moves)")


if __name__ == "__main__":
    main()
