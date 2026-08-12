#!/usr/bin/env python3
"""Does a better CONSTRUCTION survive the anneal?

Everything else about Z1 is now closed by measurement. Tardiness is exactly the
set of punctual-insert failures; brute-forcing every bay, orientation, integer
position and punctual day at the moment each failure happens finds a legal spot
in 1 of 60 cases, so the candidate vocabulary and the vp oracle are not the
cause (tools/missed.py). Delaying neighbours inside their slack opens nothing
(slack 3-4d against 28-29d stays). Finished boards admit no legal move at all.
Variance is bounded -- the best of 60 full-budget baseline draws is still +39.6%
on P4. Budget is bounded -- 5x buys -12.2% there and -0.2% on P5.

What is left is where the EARLIER blocks were put. Construction costs 0.07-0.17s
against limits of 240-900s -- 0.1% of the budget -- so if construction quality
propagates we can afford to spend a hundred times more on it. If it does not,
every construction-side change is dead on arrival, which would retire a whole
family of candidates at once (six seed orders, squeeze-first, hardest-first,
orientation policy).

This measures the propagation directly: build with each of several distinct
orders, record the construction objective, anneal each for the same budget from
its own board, and report the rank correlation between the two.

    python3 tools/transfer.py SOLVERDIR prob_4 SECONDS [seed]
"""
import json
import pathlib
import random
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent


def spearman(a, b):
    ra = {v: i for i, v in enumerate(sorted(a))}
    rb = {v: i for i, v in enumerate(sorted(b))}
    x, y = [ra[v] for v in a], [rb[v] for v in b]
    m = len(x)
    mx, my = sum(x) / m, sum(y) / m
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    den = (sum((xi - mx) ** 2 for xi in x) * sum((yi - my) ** 2 for yi in y)) ** 0.5
    return num / den if den else 0.0


def main():
    solver_dir, prob_id = sys.argv[1], sys.argv[2]
    secs = float(sys.argv[3])
    seed = int(sys.argv[4]) if len(sys.argv) > 4 else 7
    sys.path.insert(0, str(REPO / solver_dir))
    import myalgorithm as ma
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    B = prob["blocks"]
    n = len(B)
    R = [int(b["release_time"]) for b in B]
    A = [b["workload"] for b in B]

    eng0, D, P = ma._fresh(prob)
    rng = random.Random(seed)
    orders = {
        "due": sorted(range(n), key=lambda i: D[i]),
        "slack": sorted(range(n), key=lambda i: (D[i] - P[i] - R[i], -A[i])),
        "workload": sorted(range(n), key=lambda i: (-A[i], D[i])),
        "due,long": sorted(range(n), key=lambda i: (D[i], -P[i])),
        "entry-deadline": sorted(range(n), key=lambda i: D[i] - P[i]),
        "release": sorted(range(n), key=lambda i: (R[i], D[i])),
    }
    for k in range(6):
        orders[f"jitter{k}"] = sorted(range(n),
                                      key=lambda i: D[i] + rng.uniform(-8, 8))

    print(f"{prob_id}: {len(orders)} constructions, {secs:.0f}s anneal each, seed {seed}")
    print(f"   {'order':>15} {'construct Z1':>13} {'construct obj':>15} "
          f"{'final Z1':>10} {'final obj':>15}")
    rows = []
    for name, od in orders.items():
        eng, _, _ = ma._fresh(prob)
        if eng.reconstruct_ordered([-1] * n, od, False, seed) != -1:
            print(f"   {name:>15}  construction failed")
            continue
        c = eng.objective()
        eng.run_alns(secs, 0.05, 0.15, 0.002, 0.9995, seed)
        f = eng.objective()
        print(f"   {name:>15} {c[0]:13,.0f} {c[3]:15,.0f} {f[0]:10,.0f} {f[3]:15,.0f}")
        rows.append((c[0], c[3], f[0], f[3]))

    if len(rows) >= 4:
        print(f"   rank correlation construction -> final:"
              f"   objective {spearman([r[1] for r in rows], [r[3] for r in rows]):+.2f}"
              f"   Z1 {spearman([r[0] for r in rows], [r[2] for r in rows]):+.2f}")
        best_c = min(rows, key=lambda r: r[1])
        worst_c = max(rows, key=lambda r: r[1])
        print(f"   best construction ({best_c[1]:,.0f}) ends at {best_c[3]:,.0f}; "
              f"worst ({worst_c[1]:,.0f}) ends at {worst_c[3]:,.0f}"
              f"  -> spread carried through: "
              f"{(worst_c[3] - best_c[3]) / max(1, worst_c[1] - best_c[1]) * 100:.1f}%")


if __name__ == "__main__":
    main()
