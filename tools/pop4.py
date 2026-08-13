#!/usr/bin/env python3
"""The population path silently drops its diversity founder. Is it worth having?

myalgorithm.py builds `starts = [seeds[0], []] + seeds[1:3]` -- four slots, and
the EMPTY one is a deliberate signal. api.cpp:507 spells out its meaning:
"an empty vector meaning construct fresh", and run_portfolio duly calls
constructInitial for that thread. _population_search does not:

    for st0 in (starts or [[]]):
        if len(st0) != n * 7: continue      # <- the empty slot is skipped

so the population evolves from THREE founders, all of them `_seed_free`
insertion orders, and never gets the one structurally independent construction
the portfolio path gets. P3 is the only board that takes this route
(_pop_here = sec >= 150 and sec/n <= 1.6) and it is our closest board to the
target, so the missing founder is worth pricing.

Motivation from lockq: P3's q0.4 baseline returned 78,705 on three replicates
and 76,235 on the fourth -- a nearly rigid landscape where a better draw exists
but is rarely found. That is exactly the symptom of too little founder
diversity.

Arms are paired on one _seed_free per replicate, so only the founder set
differs:
    A   shipped: 3 founders
    B   4 founders, the empty slot filled by a release-order construction
        (structurally unlike every order _seed_free uses)

    python3 tools/pop4.py prob_3 220 6
"""
import json
import math
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent


def main():
    pid = sys.argv[1]
    sec = float(sys.argv[2]) if len(sys.argv) > 2 else 220.0
    REP = int(sys.argv[3]) if len(sys.argv) > 3 else 6
    ds = next(d for d in ("preliminary_test", "train_final", "train")
              if (REPO / "data" / d / f"{pid}.json").exists())
    prob = json.load(open(REPO / "data" / ds / f"{pid}.json"))
    sys.path.insert(0, str(REPO / "solver"))
    import myalgorithm as ma
    B = prob["blocks"]
    n = len(B)
    D = [int(round(b["due_date"])) for b in B]
    P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
    R = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]
    pref = [b["bay_preferences"] for b in B]
    spread = sorted(range(n), key=lambda i: -(max(pref[i]) - min(pref[i])))
    asg = None
    if ma._saturation(prob) < 0.6:
        asg = [-1] * n
        for i in spread[:int(n * 0.4)]:
            asg[i] = max(range(len(pref[i])), key=lambda j: pref[i][j])
    pop_here = sec >= 150.0 and sec / max(1, n) <= 1.6
    print(f"{pid}: n={n} sec={sec:.0f} pop_here={pop_here}"
          f" lock={'yes' if asg else 'no'}", flush=True)
    if not pop_here:
        print("   not on the population path -- this probe does not apply",
              flush=True)
        return

    # the founder the population never gets: release-order, which appears in
    # none of _seed_free's five orders (due, slack, area, due+longP, due+noise)
    extra_order = sorted(range(n), key=lambda i: (R[i], D[i], -P[i]))

    rows = []
    for rep in range(REP):
        out = {}
        for arm in ("A", "B"):
            eng, seeds = ma._seed_free(prob, asg=asg)
            if eng is None or not seeds:
                out[arm] = None
                continue
            starts = [seeds[0], []] + seeds[1:3]
            if arm == "B":
                lockv = asg if asg is not None else [-1] * n
                if eng.reconstruct_ordered(list(lockv), extra_order,
                                           False, 7) == -1:
                    eng.build_occ(eng.horizon())
                    starts = [seeds[0], eng.get_solution()] + seeds[1:3]
                eng.set_solution(list(seeds[0]))
                eng.build_occ(eng.horizon())
            eng.set_assignment_lock(asg or [])
            ma._population_search(prob, eng, 900 + rep, sec, starts, asg)
            eng.set_assignment_lock([])
            o = eng.objective()
            res = ma._check(prob, ma._recs_to_sol(ma._extract(eng, n),
                                                  eng.entry_ranks()))
            out[arm] = (o[3], res.get("feasible") if res else None)
        rows.append(out)
        a, b = out.get("A"), out.get("B")
        if a and b:
            print(f"   rep{rep}: A {a[0]:,.0f}  B {b[0]:,.0f}"
                  f" ({(b[0]-a[0])/a[0]*100:+.2f}%)"
                  f"  checker A={a[1]} B={b[1]}", flush=True)
    d = sorted((r["B"][0] - r["A"][0]) / r["A"][0] * 100
               for r in rows if r.get("A") and r.get("B"))
    if not d:
        return
    w = sum(1 for x in d if x < 0)
    med = d[len(d)//2] if len(d) % 2 else (d[len(d)//2-1]+d[len(d)//2])/2
    print(f"   {pid} 4-founder: {w}W/{len(d)-w}L median {med:+.2f}%"
          + ("   <-- BEATS 3 FOUNDERS" if med < 0 else ""), flush=True)


if __name__ == "__main__":
    main()
