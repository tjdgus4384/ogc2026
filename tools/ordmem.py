#!/usr/bin/env python3
"""The decisive test for (bay, order) crossover: is a child worth the anneal?

ordx established the mechanism -- 537 of 537 crossovers realised across four
boards, because the encoding never names a coordinate. But every child sits
+104% to +432% above its ANNEALED parents: the operator lives at seed level,
where the anneal's own 52-88% descent dwarfs a 1-29% seed gain.

So the mechanism is not the question. The question is opportunity cost:

    given T more seconds, is it better to keep annealing a parent,
    or to cross two parents and anneal the child?

Both arms get the SAME T on the SAME board from the same engine, so this is a
fair paired comparison and not raw-vs-annealed (instrument defect 5). Baseline
is min over the parents -- the child must beat the BEST parent's continuation,
not the average, because a real run would keep the best.

    python3 tools/ordmem.py prob_5 600 7 11 120
"""
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
THR = 8


def main():
    prob_id, tl = sys.argv[1], float(sys.argv[2])
    sa, sb = int(sys.argv[3]), int(sys.argv[4])
    T = float(sys.argv[5]) if len(sys.argv) > 5 else 120.0
    sys.path.insert(0, str(REPO / "tools"))
    from board import get_board
    import ordx
    A, D, P, eng, hz, prob = get_board(prob_id, tl, sa)
    n = len(prob["blocks"])
    hit = list((REPO / "runs" / "boards").glob(f"{prob_id}_tl{int(tl)}_s{sb}_*.json"))
    if not hit:
        sys.exit(f"no cached board for seed {sb}")
    B = json.loads(hit[0].read_text())["base"]

    def anl(board, seed):
        eng.set_solution(list(board))
        eng.build_occ(hz)
        eng.run_portfolio(THR, T, 1, 0.05, 0.15, 0.002, 0.9995, seed, 1,
                          [eng.get_solution()])
        return eng.objective()[3], eng.get_solution()

    def obj(flat):
        eng.set_solution(flat)
        eng.build_occ(hz)
        return eng.objective()[3]
    oa, ob = obj(A), obj(B)
    ba, ra, ea = ordx.enc(A, n)
    bb, rb, eb = ordx.enc(B, n)

    # rebuild the best child exactly as ordx does
    best = (None, None, None)
    plans = []
    Tm = max(x for x in ea if x < 10**9)
    for t in range(0, Tm + 1, max(1, Tm // 30)):
        plans.append((f"time<{t}", [i for i in range(n) if ea[i] < t]))
    for j in range(len(prob["bays"])):
        plans.append((f"bayA={j}", [i for i in range(n) if ba[i] == j]))
    for nm, m in plans:
        S = set(m)
        for mode in ("both", "bay", "ord"):
            bx = [ba[i] if (i in S and mode != "ord") else bb[i] for i in range(n)]
            rx = [ra[i] if (i in S and mode != "bay") else rb[i] for i in range(n)]
            r = ordx.build(eng, n, hz, bx, rx)
            if r and r[1] == n and (best[0] is None or r[0] < best[0]):
                eng.build_occ(hz)
                best = (r[0], f"{nm}/{mode}", eng.get_solution())
    if best[0] is None:
        sys.exit("no child realised")
    print(f"{prob_id} s{sa}/s{sb}  T={T:.0f}s")
    print(f"   parents {oa:,.0f} / {ob:,.0f}   child {best[0]:,.0f} ({best[1]})")

    ca, _ = anl(A, 101)
    cb, _ = anl(B, 102)
    cc, cs = anl(best[2], 103)
    keep = min(ca, cb)
    print(f"   +{T:.0f}s on parent A: {oa:,.0f} -> {ca:,.0f} ({(ca-oa)/oa*100:+.2f}%)")
    print(f"   +{T:.0f}s on parent B: {ob:,.0f} -> {cb:,.0f} ({(cb-ob)/ob*100:+.2f}%)")
    print(f"   +{T:.0f}s on child   : {best[0]:,.0f} -> {cc:,.0f}"
          f" ({(cc-best[0])/best[0]*100:+.2f}%)")
    d = (cc - keep) / keep * 100
    print(f"   => child {d:+.2f}% vs best continued parent"
          + ("   <-- CROSSOVER WINS" if d < 0 else ""))
    sys.path.insert(0, str(REPO / "solver"))
    import myalgorithm as ma
    res = ma._check(prob, ma._recs_to_sol(ma._extract(eng, n), eng.entry_ranks()))
    print(f"      child checker={res.get('feasible') if res else None}")


if __name__ == "__main__":
    main()
