#!/usr/bin/env python3
"""Squeaky Wheel Optimization over the real greedy: learn WHO yields.

The diagnosis this answers is the campaign's central one: tardiness is pure
contention -- insertGreedy gives every block its own earliest feasible day in
a fixed priority order, and whoever arrives when space is gone eats the
lateness. The sacrifice order is an artifact, never optimized. The master
tried to optimize it in a relaxation and died on realization; jointcon
replaced the greedy and only pays on congested boards. SWO keeps the greedy
untouched -- every construction is geometrically sound by construction -- and
optimizes only the PRIORITY ORDER, by feedback:

    construct in priority order -> blocks that came out tardy get their
    priority boosted (they insert earlier next round, someone else yields)
    -> reconstruct -> repeat, keeping the best board seen.

This is not ord1/ord2 (static order families, refuted): the order here is
learned from each construction's actual failures. Sixty rounds cost seconds.

Judgment, same ladder as every constructor: seed quality against _seed_free's
best-of-5 (and jointcon's 37.1M on P6); if it leads, the bare-400s plateau
test; if that leads, the driver gate.

    python3 tools/swo.py prob_6 [rounds] [seed] [anneal_sec]
"""
import json
import math
import os
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
SOLVER = "builds/pc2"


def main():
    prob_id = sys.argv[1]
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 7
    anneal = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0
    sys.path.insert(0, str(REPO / SOLVER))
    os.environ["OGC_SEED"] = str(seed)
    import myalgorithm as ma
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    B = prob["blocks"]
    n = len(B)

    eng0, seeds = ma._seed_free(prob)
    o0 = eng0.objective()
    print(f"{prob_id} seed {seed}: _seed_free best-of-5 {o0[3]:,.0f}  Z1={o0[0]:.0f}")

    eng, D, P = ma._fresh(prob, 4)
    hz = eng.horizon()
    # priority = due date, the greedy's own base order; tardiness feedback
    # subtracts from it, moving the squeaky block earlier next round.
    prio = {i: float(D[i]) for i in range(n)}
    best = None
    best_flat = None
    t0 = time.time()
    r = 0
    while r < rounds:
        r += 1
        eng.set_solution([0] * (n * 7))
        eng.build_occ(hz)
        order = sorted(range(n), key=lambda i: (prio[i], -P[i]))
        ok = True
        for i in order:
            if not eng.insert_capped(i, -1, -1):
                ok = False
                break
        if not ok:
            continue
        o = eng.objective()
        if best is None or o[3] < best[3]:
            best = o
            best_flat = eng.get_solution()
        # the squeak: every tardy day pulls the block earlier in the queue.
        # step = 2 days of priority per tardy day, decaying nothing -- the
        # simplest rule first; refinements only if the simple one moves.
        moved = 0
        for i in range(n):
            b = eng.get_block(i)
            if b[6] and b[5] > D[i]:
                prio[i] -= 2.0 * (b[5] - D[i])
                moved += 1
        if moved == 0:
            break
    el = time.time() - t0
    print(f"   SWO best of {r} rounds: {best[3]:,.0f}  Z1={best[0]:.0f}"
          f" Z2={best[1]:.0f} Z3={best[2]:.0f}"
          f"   vs seed {(best[3]-o0[3])/o0[3]*100:+.2f}%   [{el:.0f}s]")
    if anneal > 0 and best_flat is not None:
        eng.set_solution(best_flat)
        eng.build_occ(hz)
        eng.run_portfolio(4, anneal, 1, 0.05, 0.15, 0.002, 0.9995, seed, 1,
                          [best_flat])
        o2 = eng.objective()
        print(f"   + bare {anneal:.0f}s portfolio -> {o2[3]:,.0f}  Z1={o2[0]:.0f}"
              f"   (plateau reference 28.2-28.9M on P6)")


if __name__ == "__main__":
    main()
