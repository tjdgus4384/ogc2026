#!/usr/bin/env python3
"""S5: rollout construction as a BUDGET CLAIMANT, not a start hedge.

F4 is now demonstrated four ways: a deterministically better seed is recovered
by the driver, because both arms then anneal the same total time and the
plateau is start-invariant. The spec's answer is not another start -- it is to
let the rollout construction claim budget the anneal would otherwise spend,
so the comparison is no longer "same anneal, different start" but "less anneal
from a much better place".

Both arms get the identical wall budget. Control spends it all on
seeds+portfolio. Candidate spends `frac` of it on the rollout-defer
construction and the rest annealing from there. If the plateau is reached from
both, the candidate wins nothing (and F4 extends to budget splits). If the
better construction reaches a lower plateau OR reaches the same one with time
to spare that the control lacks, the split shows it here and nowhere else.

    python3 tools/s5_split.py prob_5 TOTAL_SEC FRAC [seed]
"""
import json, os, pathlib, sys, time
REPO = pathlib.Path(__file__).resolve().parent.parent
SOLVER = pathlib.Path(os.environ.get("OGC_S5_SOLVER", "builds/pc2"))


def portfolio(eng, seconds, seed, starts):
    """Spend only the caller's remaining wall budget."""
    if seconds <= 0.05:
        return
    eng.run_portfolio(4, seconds, 1, 0.05, 0.15, 0.002, 0.9995,
                      seed, 1, starts)


def checked_objective(ma, prob, eng, n):
    """Production-gated objective; engine-only scores are not evidence."""
    _sol, result = ma._gate(prob, eng, n)
    if result is None or not result.get("feasible"):
        raise RuntimeError(f"checker gate failed: {result}")
    return float(result["objective"])

def main():
    pid = sys.argv[1]; total = float(sys.argv[2])
    frac = float(sys.argv[3]); seed = int(sys.argv[4]) if len(sys.argv) > 4 else 7
    solver = SOLVER if SOLVER.is_absolute() else REPO / SOLVER
    sys.path.insert(0, str(solver)); os.environ["OGC_SEED"] = str(seed)
    import myalgorithm as ma
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{pid}.json"))
    n = len(prob["blocks"])

    # ---- control: seeds + portfolio for the whole budget
    a0 = time.monotonic()
    engA, seeds = ma._seed_free(prob)
    startsA = [seeds[0], []] + seeds[1:3] if seeds else []
    setupA = time.monotonic() - a0
    portfolio(engA, max(0.0, total - setupA), seed, startsA)
    wallA = time.monotonic() - a0
    oA = engA.objective()
    gateA0 = time.monotonic()
    cA = checked_objective(ma, prob, engA, n)
    gateA = time.monotonic() - gateA0
    print(f"{pid} seed {seed} budget {total:.0f}s:"
          f" control {cA:,.0f}  kernel={oA[3]:,.0f} Z1={oA[0]:.0f}"
          f" setup={setupA:.1f}s wall={wallA:.1f}s gate={gateA:.1f}s")

    # ---- candidate: rollout-defer construction for frac, anneal the rest
    t0 = time.monotonic()
    eng, D, P = ma._fresh(prob, 4)
    bo = sorted(range(n), key=lambda i: (D[i], -P[i]))
    off = {}
    def build(extra=None):
        os_ = dict(off)
        if extra: os_.update(extra)
        sq = [i for _, _, i in sorted((k + os_.get(i, 0), k, i)
                                      for k, i in enumerate(bo))]
        if eng.reconstruct_ordered([-1] * n, sq, False, 7) != -1:
            return None, None
        td = [i for i in sq if eng.get_block(i)[6]
              and eng.get_block(i)[5] > D[i]]
        return eng.objective(), td
    budget = total * frac
    cur, td = build()
    dec = set()
    while cur is not None and time.monotonic() - t0 < budget:
        nx = next((i for i in td if i not in dec), None)
        if nx is None: break
        dec.add(nx)
        cc = {0: cur}
        for d in (5, 15, 40):
            o, _ = build({nx: off.get(nx, 0) + d})
            if o is not None: cc[d] = o
        bd = min(cc, key=lambda d: cc[d][3])
        if bd != 0: off[nx] = off.get(nx, 0) + bd
        cur, td = build()
    cons = time.monotonic() - t0
    o1 = eng.objective()
    flat = eng.get_solution()
    rest = max(0.0, total - cons)
    portfolio(eng, rest, seed, [flat])
    wallB = time.monotonic() - t0
    oB = eng.objective()
    gateB0 = time.monotonic()
    cB = checked_objective(ma, prob, eng, n)
    gateB = time.monotonic() - gateB0
    print(f"   candidate: construct {cons:.0f}s -> {o1[3]:,.0f},"
          f" anneal {rest:.0f}s -> {cB:,.0f}"
          f"  kernel={oB[3]:,.0f} Z1={oB[0]:.0f} wall={wallB:.1f}s"
          f" gate={gateB:.1f}s"
          f"   vs control {(cB-cA)/cA*100:+.2f}%")

if __name__ == "__main__":
    main()
