#!/usr/bin/env python3
"""Is a blown-up run detectable EARLY, while there is still budget to restart?

The loss is in the right tail, not the average. Pooling every baseline run in
runs/abgate shows P5's tardiness is normally 704-808 but seed 101 lands
1,237-1,462 on all three of its runs and seed 89 splits 746 / 1,243 -- a 60-90%
blow-up on the term that is 95% of P5's objective, worth ~8.7M when it fires.
P4 spans 152-202 across seeds. P3 never moves (Z1 = 0, 48/48).

Blind restarts are already refuted: the time curve is steep below 1x, so
splitting the budget into independent runs loses. An ADAPTIVE restart is a
different trade -- it spends nothing on the runs that are fine. It only works
if the disaster is visible early, so this measures exactly that: for one
instance across many seeds, record the tardiness of the SEED CONSTRUCTION and
of the first anneal chunk, then the final, and report how well the early value
orders the final one.

If the early signal ranks the finals, a run can be abandoned at 15-25% of the
budget on evidence. If it does not, the blow-up is decided later than any
restart could act on, and this axis is closed.

    python3 tools/blowup.py SOLVERDIR prob_5 SECONDS seed1,seed2,...
"""
import json
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent


def main():
    solver_dir, prob_id = sys.argv[1], sys.argv[2]
    secs = float(sys.argv[3])
    seeds = [int(s) for s in sys.argv[4].split(",")]
    sys.path.insert(0, str(REPO / solver_dir))
    import myalgorithm as ma
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    n = len(prob["blocks"])

    print(f"{prob_id}: {len(seeds)} seeds, {secs:.0f}s each")
    print(f"   {'seed':>5} {'construct Z1':>13} {'@15% Z1':>10} {'@30% Z1':>10} "
          f"{'final Z1':>10} {'final obj':>14}")
    rows = []
    for sd in seeds:
        eng, D, P = ma._fresh(prob)
        order = sorted(range(n), key=lambda i: (D[i], -P[i]))
        if eng.reconstruct_ordered([-1] * n, order, False, sd) != -1:
            print(f"   {sd:>5}  construction failed")
            continue
        z_c = eng.objective()[0]
        eng.run_alns(secs * 0.15, 0.05, 0.15, 0.002, 0.9995, sd)
        z_15 = eng.objective()[0]
        eng.run_alns(secs * 0.15, 0.05, 0.15, 0.002, 0.9995, sd + 1)
        z_30 = eng.objective()[0]
        eng.run_alns(secs * 0.70, 0.05, 0.15, 0.002, 0.9995, sd + 2)
        o = eng.objective()
        print(f"   {sd:>5} {z_c:13,.0f} {z_15:10,.0f} {z_30:10,.0f} "
              f"{o[0]:10,.0f} {o[3]:14,.0f}")
        rows.append((z_c, z_15, z_30, o[0], o[3]))

    if len(rows) < 3:
        return

    def spearman(a, b):
        ra = {v: i for i, v in enumerate(sorted(a))}
        rb = {v: i for i, v in enumerate(sorted(b))}
        x = [ra[v] for v in a]
        y = [rb[v] for v in b]
        m = len(x)
        mx, my = sum(x) / m, sum(y) / m
        num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
        den = (sum((xi - mx) ** 2 for xi in x) * sum((yi - my) ** 2 for yi in y)) ** 0.5
        return num / den if den else 0.0

    fin = [r[3] for r in rows]
    for k, label in ((0, "construction"), (1, "@15%"), (2, "@30%")):
        print(f"   rank correlation {label:>12} vs final Z1: "
              f"{spearman([r[k] for r in rows], fin):+.2f}")


if __name__ == "__main__":
    main()
