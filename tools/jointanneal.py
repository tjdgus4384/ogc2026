#!/usr/bin/env python3
"""Stage (b): does jointcon's seed advantage survive the anneal?

jointcon v0 beats the kernel's best-of-5 seed on P6 by 13.95% (Z1 5349 vs
6287) -- the first constructor ever to do so, on the board where congestion is
worst and the per-day joint-seating lever should matter most. The ledger's
standing caution is that construction quality tends to wash out ("ord1:
좋게 만들어도 파이프라인이 떠난다"), so the question is whether THIS kind of
seed -- structurally denser days, not a reshuffled insertion order -- moves
the basin the anneal lands in.

Both arms get the same portfolio call and the same wall budget; the only
difference is the starts. Deterministic given the seed.

    python3 tools/jointanneal.py prob_6 SECONDS [seed]
"""
import json, math, os, pathlib, sys, time
REPO = pathlib.Path(__file__).resolve().parent.parent
SOLVER = "builds/pc2"

def main():
    prob_id = sys.argv[1]
    sec = float(sys.argv[2])
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 7
    sys.path.insert(0, str(REPO / SOLVER))
    os.environ["OGC_SEED"] = str(seed)
    import myalgorithm as ma
    from ortools.sat.python import cp_model as cp
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    B = prob["blocks"]; n = len(B)
    R = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]

    # ---- arm A: normal seeds -> portfolio
    engA, seeds = ma._seed_free(prob)
    oA0 = engA.objective()
    starts = [seeds[0], []] + seeds[1:3] if seeds else []
    engA.run_portfolio(4, sec, 1, 0.05, 0.15, 0.002, 0.9995, seed, 1, starts)
    oA = engA.objective()
    print(f"{prob_id} A(normal): seed {oA0[3]:,.0f} -> {oA[3]:,.0f}"
          f"  Z1={oA[0]:.0f}")

    # ---- arm B: jointcon board -> portfolio
    eng, D, P = ma._fresh(prob, 4)
    hz = eng.horizon()
    eng.set_solution([0] * (n * 7)); eng.build_occ(hz)
    unplaced = set(range(n)); H = max(D) + 64; day = 0
    while unplaced and day <= H:
        want = [i for i in unplaced if R[i] <= day]
        if not want: day += 1; continue
        CAND, flat, idx = {}, [], []
        for i in want:
            raw = eng.feasible_positions(i, 6, day)
            cs = [tuple(raw[k:k+5]) for k in range(0, len(raw), 5)]
            cs = [c for c in cs if c[4] == day]
            if cs:
                CAND[i] = cs
                for k, c in enumerate(cs):
                    flat += [i, c[0], c[1], c[2], c[3], c[4]]; idx.append((i, k))
        if not CAND: day += 1; continue
        mdl = cp.CpModel(); V, use = {}, {}
        for i in CAND:
            V[i] = [mdl.NewBoolVar(f"z{i}_{k}") for k in range(len(CAND[i]))]
            use[i] = mdl.NewBoolVar(f"u{i}"); mdl.Add(sum(V[i]) == use[i])
        prs = eng.pair_conflicts(flat)
        for q in range(0, len(prs), 2):
            (a1, k1), (a2, k2) = idx[prs[q]], idx[prs[q+1]]
            mdl.AddBoolOr([V[a1][k1].Not(), V[a2][k2].Not()])
        wgt = {i: (1000 if (D[i]-P[i])-day <= 0 else max(1, 1000//(1+(D[i]-P[i])-day)))
               for i in CAND}
        mdl.Maximize(sum(wgt[i]*use[i] for i in CAND))
        sv = cp.CpSolver(); sv.parameters.max_time_in_seconds = 6.0
        sv.parameters.num_search_workers = 4
        if sv.Solve(mdl) in (cp.OPTIMAL, cp.FEASIBLE):
            for i in list(CAND):
                if sv.Value(use[i]):
                    for k, c in enumerate(CAND[i]):
                        if sv.Value(V[i][k]):
                            if eng.place_at(i, c[0], c[1], c[2], c[3], c[4]):
                                unplaced.discard(i)
                            break
        day += 1
    oB0 = eng.objective()
    jb = eng.get_solution()
    eng.run_portfolio(4, sec, 1, 0.05, 0.15, 0.002, 0.9995, seed, 1, [jb])
    oB = eng.objective()
    print(f"{prob_id} B(jointcon): seed {oB0[3]:,.0f} -> {oB[3]:,.0f}"
          f"  Z1={oB[0]:.0f}")
    print(f"   final delta B vs A: {(oB[3]-oA[3])/oA[3]*100:+.2f}%"
          f"   (seed delta was {(oB0[3]-oA0[3])/oA0[3]*100:+.2f}%)")

if __name__ == "__main__":
    main()
