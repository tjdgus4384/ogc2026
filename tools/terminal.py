#!/usr/bin/env python3
"""At the end of a run, are there improving moves left in the neighbourhood?

The anneal does real work -- P4's construction scores 8,040,292 and its final
board 3,005,740, a 2.7x improvement -- so the question is not whether the search
moves, it is why it stops where it does. Exactly two things can be true when it
stops:

  LOCAL OPTIMUM   no improving move exists in the neighbourhood it samples
                  (destroy 5-15% of the blocks, repair first-fit), so no amount
                  of extra search or better acceptance can help and the
                  neighbourhood itself has to change;
  SEARCH FAILURE  improving moves do exist and the sampler is not finding them,
                  which is a search problem, not a neighbourhood one.

These call for opposite work, and nothing in this repo has measured which holds.
This does: anneal to convergence, then draw many independent destroy-repair
moves from the converged board and count how many improve the true objective.

The move is drawn exactly as alns::run draws it -- a random removal set of the
same size, repaired by the same insertGreedy -- so a hit rate here is the hit
rate the real search sees at the end of its budget.

    python3 tools/terminal.py SOLVERDIR prob_4 ANNEAL_SECS TRIALS [seed]
"""
import json
import pathlib
import random
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent


def main():
    solver_dir, prob_id = sys.argv[1], sys.argv[2]
    anneal = float(sys.argv[3])
    trials = int(sys.argv[4])
    seed = int(sys.argv[5]) if len(sys.argv) > 5 else 7
    sys.path.insert(0, str(REPO / solver_dir))
    import myalgorithm as ma
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    n = len(prob["blocks"])

    # horizon ladder, as _seed_free does: on congested giants (P6) the loose
    # insert overflows the base occupancy horizon and the construction fails
    # outright, so doubling it is part of the shipping construction, not a
    # probe convenience.
    eng = None
    for hmul in (1, 2, 4):
        eng, D, P = ma._fresh(prob, hmul)
        order = sorted(range(n), key=lambda i: (D[i], -P[i]))
        if eng.reconstruct_ordered([-1] * n, order, False, seed) == -1:
            break
        eng = None
    if eng is None:
        print(f"{prob_id}: construction failed at every horizon")
        return
    c = eng.objective()
    eng.run_alns(anneal, 0.05, 0.15, 0.002, 0.9995, seed)
    base_state = eng.get_solution()
    base = eng.objective()
    hz = eng.horizon()
    rng = random.Random(seed)
    placed = [i for i in range(n) if base_state[i * 7 + 6]]

    print(f"{prob_id}: construction {c[3]:,.0f} -> annealed {base[3]:,.0f} "
          f"({anneal:.0f}s, seed {seed}), {len(placed)} placed")

    # q as alns::run draws it: 5-15% of n for the ordinary neighbourhood
    for frac in (0.05, 0.15, 0.30, 0.60):
        q = max(2, int(frac * n))
        better = equal = infeasible = 0
        best = 0.0
        t0 = time.time()
        for _ in range(trials):
            eng.set_solution(base_state)
            eng.build_occ(hz)
            victims = rng.sample(placed, min(q, len(placed)))
            st = list(base_state)
            for i in victims:
                st[i * 7 + 6] = 0
            eng.set_solution(st)
            eng.build_occ(hz)
            ok = True
            # hardest-first, the order alns::run repairs in
            for i in sorted(victims, key=lambda k: -P[k]):
                if not eng.insert_capped(i, -1, -1):
                    ok = False
                    break
            if not ok:
                infeasible += 1
                continue
            v = eng.objective()[3]
            if v < base[3] - 1e-9:
                better += 1
                best = min(best, (v - base[3]) / base[3] * 100)
            elif abs(v - base[3]) <= 1e-9:
                equal += 1
        el = time.time() - t0
        print(f"   destroy {frac*100:4.0f}% (q={q:3d}): {better:5d}/{trials} improve"
              f"  ({better/trials*100:5.2f}%)   {equal:5d} equal   "
              f"{infeasible:5d} could not rebuild   best {best:+.2f}%  [{el:.0f}s]")
    eng.set_solution(base_state)
    eng.build_occ(hz)


if __name__ == "__main__":
    main()
