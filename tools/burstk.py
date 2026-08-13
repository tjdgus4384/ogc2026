#!/usr/bin/env python3
"""Sweep the population's burst divisor -- founders vs generations.

_population_search spends `burst = max(20.0, sec/8.0)` on each founder and each
child. On P3 (sec 220) that is 27.5s: three founders eat 82.5s and only ~5
children get the remaining 137s. The founders all come from _seed_free's
insertion orders, so they are highly correlated -- and pop4's first replicate
says ADDING a fourth costs +4.96%. If founders are the expensive part and the
children are where the value is, the divisor is the knob: a larger K means
shorter bursts and more generations.

The 8 is a bare constant with no measurement behind it in the ledger. P3 is the
only board on this path and our closest to the target, so it is worth pricing.

Paired on one _seed_free per replicate; only the divisor differs. The shipped
K=8 stays first as the baseline.

    OGC_BURSTK=8,6,12,16 python3 tools/burstk.py prob_3 220 5

Uses builds/burstk, which is v6.4.1 plus an OGC_BURSTDIV knob (unset = sec/8.0).
"""
import json, math, os, pathlib, sys, time
REPO = pathlib.Path(__file__).resolve().parent.parent
KS = tuple(float(x) for x in os.environ.get("OGC_BURSTK", "8,6,12,16").split(","))


def main():
    pid = sys.argv[1]
    sec = float(sys.argv[2]) if len(sys.argv) > 2 else 220.0
    REP = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    ds = next(d for d in ("preliminary_test", "train_final", "train")
              if (REPO / "data" / d / f"{pid}.json").exists())
    prob = json.load(open(REPO / "data" / ds / f"{pid}.json"))
    sys.path.insert(0, str(REPO / "builds" / "burstk"))
    import myalgorithm as ma
    B = prob["blocks"]; n = len(B)
    pref = [b["bay_preferences"] for b in B]
    spread = sorted(range(n), key=lambda i: -(max(pref[i]) - min(pref[i])))
    asg = None
    if ma._saturation(prob) < 0.6:
        asg = [-1] * n
        for i in spread[:int(n * 0.4)]:
            asg[i] = max(range(len(pref[i])), key=lambda j: pref[i][j])
    print(f"{pid}: n={n} sec={sec:.0f} shipped burst="
          f"{max(20.0, sec/8.0):.1f}s", flush=True)

    rows = []
    for rep in range(REP):
        out = {}
        for K in KS:
            eng, seeds = ma._seed_free(prob, asg=asg)
            if eng is None or not seeds:
                out[K] = None
                continue
            starts = [seeds[0], []] + seeds[1:3]
            eng.set_assignment_lock(asg or [])
            os.environ["OGC_BURSTDIV"] = f"{K:g}"
            ma._population_search(prob, eng, 900 + rep, sec, starts, asg)
            eng.set_assignment_lock([])
            o = eng.objective()
            res = ma._check(prob, ma._recs_to_sol(ma._extract(eng, n),
                                                  eng.entry_ranks()))
            out[K] = (o[3], res.get("feasible") if res else None)
        rows.append(out)
        b = out.get(KS[0])
        print(f"   rep{rep}: " + "   ".join(
            f"K{K:g} " + ("FAIL" if out[K] is None else
                          f"{out[K][0]:,.0f}"
                          + ("" if K == KS[0] or not b else
                             f" ({(out[K][0]-b[0])/b[0]*100:+.2f}%)"))
            for K in KS), flush=True)
    for K in KS[1:]:
        d = sorted((r[K][0] - r[KS[0]][0]) / r[KS[0]][0] * 100
                   for r in rows if r.get(K) and r.get(KS[0]))
        if not d:
            continue
        w = sum(1 for x in d if x < 0)
        med = d[len(d)//2] if len(d) % 2 else (d[len(d)//2-1]+d[len(d)//2])/2
        print(f"   {pid} K={K:g}: {w}W/{len(d)-w}L median {med:+.2f}%"
              + (f"   <-- BEATS K={KS[0]:g}" if med < 0 else ""), flush=True)


if __name__ == "__main__":
    main()
