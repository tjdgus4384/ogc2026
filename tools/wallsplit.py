#!/usr/bin/env python3
"""Where does the wall clock actually go? A timed trace of one shipped run.

The driver's budget arithmetic is deterministic (reserve, 30% slice carve,
rounds), but two stages overrun their nominal budget: `run_portfolio` does its
post-round spatialCompact/compactLeft OUTSIDE the anneal's wall budget, and
`_assign_descent` is handed `deadline` rather than `deadline - reserve`. This
wraps the driver's own stage functions and prints exclusive wall time per stage
so the nominal split can be checked against the real one.

    python3 tools/wallsplit.py prob_4 480 7
"""
import json
import os
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "solver"))
import myalgorithm as ma                                   # noqa: E402

EV = []          # (label, t_start, t_end, note)
T0 = None


def _wrap(mod, name, label):
    fn = getattr(mod, name)

    def g(*a, **k):
        t = time.time()
        try:
            return fn(*a, **k)
        finally:
            EV.append((label, t - T0, time.time() - T0, ""))
    setattr(mod, name, g)


class Proxy:
    """forwards everything to the pybind Engine, times the heavy calls"""

    def __init__(self, e):
        object.__setattr__(self, "_e", e)

    def __getattr__(self, k):
        a = getattr(object.__getattribute__(self, "_e"), k)
        if k not in ("run_portfolio", "build_occ"):
            return a

        def f(*args, **kw):
            t = time.time()
            r = a(*args, **kw)
            note = f"budget={args[1]:.1f}s" if k == "run_portfolio" else ""
            EV.append((k, t - T0, time.time() - T0, note))
            return r
        return f


def main():
    pid = sys.argv[1] if len(sys.argv) > 1 else "prob_4"
    tl = float(sys.argv[2]) if len(sys.argv) > 2 else 480.0
    global T0
    for ds in ("preliminary_test", "train_final", "train"):
        f = REPO / "data" / ds / f"{pid}.json"
        if f.exists():
            break
    prob = json.load(open(f))
    n = len(prob["blocks"])

    _seed_free = ma._seed_free

    def seed_free(*a, **k):
        t = time.time()
        eng, starts = _seed_free(*a, **k)
        EV.append(("_seed_free", t - T0, time.time() - T0, ""))
        return (Proxy(eng) if eng is not None else None), starts
    ma._seed_free = seed_free
    for nm, lb in (("_make_floor", "_make_floor"),
                   ("_slice_refine", "_slice_refine"),
                   ("_assign_descent", "_assign_descent"),
                   ("_gate", "_gate"),
                   ("_population_search", "_population_search")):
        _wrap(ma, nm, lb)

    T0 = time.time()
    sol = ma.algorithm(prob, timelimit=tl)
    wall = time.time() - T0
    res = ma._check(prob, sol)
    print(f"# {pid} TL={tl:.0f} n={n} m={len(prob['bays'])} "
          f"wall={wall:.1f}s obj={res.get('objective') if res else None} "
          f"feasible={res.get('feasible') if res else None}")
    print(f"{'stage':<18}{'start':>9}{'end':>9}{'dur':>9}   note")
    for lb, a, b, note in EV:
        print(f"{lb:<18}{a:>9.2f}{b:>9.2f}{b - a:>9.2f}   {note}")
    # exclusive time: subtract nested children from each parent
    print()
    tot = {}
    for lb, a, b, _ in EV:
        inner = sum(bb - aa for l2, aa, bb, _ in EV
                    if (l2, aa, bb) != (lb, a, b) and aa >= a and bb <= b)
        tot[lb] = tot.get(lb, 0.0) + (b - a) - inner
    acct = sum(tot.values())
    for lb, v in sorted(tot.items(), key=lambda kv: -kv[1]):
        print(f"exclusive {lb:<18}{v:>8.1f}s  {100 * v / wall:5.1f}% of wall")
    print(f"exclusive {'UNACCOUNTED':<18}{wall - acct:>8.1f}s  "
          f"{100 * (wall - acct) / wall:5.1f}% of wall")


if __name__ == "__main__":
    main()
