#!/usr/bin/env python3
"""Sweep the preference-lock fraction -- the constant that owns P3's objective.

zsplit: P3's z1 is EXACTLY 0, so 80.9% of its objective is preference regret and
19.1% is bay imbalance. The driver's only preference lever is one constant --

    myalgorithm.py:722   for i in spread[:int(nn * 0.4)]:  asg[i] = argmax pref

the top 40% of blocks by preference SPREAD pinned to their favourite bay. That
40% was set on 07-30 and has not been swept since; v6.4's reservation has moved
every board underneath it, and asgmove has since shown the trade is priced by
z1, which is exactly 0 on P3 and so cannot get worse there by construction.

Arms are the fraction itself, paired on one seeding per replicate. The board's
own path is used, not a probe substitute: P3 satisfies
`_pop_here = sec >= 150 and sec/n <= 1.6`, so it evolves through
_population_search, and running run_portfolio here instead would measure a
route P3 never takes.

    python3 tools/lockq.py prob_3 220 4
"""
import json
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
QS = (0.4, 0.6, 0.8, 1.0)


def main():
    pid = sys.argv[1]
    sec = float(sys.argv[2]) if len(sys.argv) > 2 else 220.0
    REP = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    ds = next(d for d in ("preliminary_test", "train_final", "train")
              if (REPO / "data" / d / f"{pid}.json").exists())
    prob = json.load(open(REPO / "data" / ds / f"{pid}.json"))
    sys.path.insert(0, str(REPO / "solver"))
    import myalgorithm as ma
    n = len(prob["blocks"])
    pref = [b["bay_preferences"] for b in prob["blocks"]]
    spread = sorted(range(n), key=lambda i: -(max(pref[i]) - min(pref[i])))
    pop_here = sec >= 150.0 and sec / max(1, n) <= 1.6
    print(f"{pid}: n={n}  sec={sec:.0f}  path="
          f"{'population' if pop_here else 'portfolio'}  sat="
          f"{ma._saturation(prob):.3f}", flush=True)

    def lock(q):
        a = [-1] * n
        for i in spread[:int(n * q)]:
            a[i] = max(range(len(pref[i])), key=lambda j: pref[i][j])
        return a

    rows = []
    for rep in range(REP):
        out = {}
        for q in QS:
            asg = lock(q)
            eng, seeds = ma._seed_free(prob, asg=asg)
            if eng is None:
                out[q] = None
                continue
            starts = [seeds[0], []] + seeds[1:3] if seeds else []
            eng.set_assignment_lock(asg)
            t0 = time.time()
            if pop_here:
                ma._population_search(prob, eng, 900 + rep, sec, starts, asg)
            else:
                eng.run_portfolio(ma._THR, sec, 1, 0.05, 0.15, 0.002, 0.9995,
                                  900 + rep, 1, starts)
            eng.set_assignment_lock([])
            o = eng.objective()
            res = ma._check(prob, ma._recs_to_sol(ma._extract(eng, n),
                                                  eng.entry_ranks()))
            out[q] = (o[3], o[0], res.get("feasible") if res else None,
                      time.time() - t0)
        rows.append(out)
        b = out.get(0.4)
        print(f"   rep{rep}: " + "   ".join(
            f"q{q:.1f} " + ("FAIL" if out[q] is None else
                            f"{out[q][0]:,.0f}"
                            + ("" if q == 0.4 or not b else
                               f" ({(out[q][0]-b[0])/b[0]*100:+.2f}%)")
                            + ("" if out[q][2] else "!"))
            for q in QS), flush=True)
    base = [r[0.4] for r in rows]
    for q in QS[1:]:
        d = sorted((r[q][0] - r[0.4][0]) / r[0.4][0] * 100
                   for r in rows if r[q] and r[0.4])
        if not d:
            continue
        w = sum(1 for x in d if x < 0)
        med = d[len(d)//2] if len(d) % 2 else (d[len(d)//2-1]+d[len(d)//2])/2
        print(f"   {pid} q={q}: {w}W/{len(d)-w}L median {med:+.2f}%"
              + ("   <-- BEATS 0.4" if med < 0 else ""), flush=True)


if __name__ == "__main__":
    main()
