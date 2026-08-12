#!/usr/bin/env python3
"""Exact recombination of the portfolio's own thread boards.

The plateau's measured signature is structural diversity at equal value: bay
assignments agree on only 13-33% of blocks across draws, tardy sets intersect
at 24-52%. A block punctual in board A is tardy in board B and vice versa, so
a per-block selection of the better treatment can dominate both parents. The
memetic attempt (c1) failed at this with consensus locks and re-annealing;
this is different -- EXACT selection: one candidate per parent per block,
compatibility from pair_conflicts (pc2's sound model), the true objective
minimized by CP. Models are tiny (n blocks x <=4 candidates).

The parents are free. Every portfolio run already produces four thread boards
under different personas -- structural diversity by design -- and the driver
takes the min and discards the rest. get_candidates() hands them over.

    recombination < min(parents)   the first mechanism ever measured to land
                                   below what the driver already computes --
                                   wire it as a strictly-gated post-pass;
    recombination = min(parents)   the parents' diversity is not exploitable
                                   per-block, and the plateau claim gains its
                                   final piece.

    python3 tools/recomb.py prob_4 TL [seed]
"""
import json
import os
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
SOLVER = "builds/pc2"


def main():
    prob_id = sys.argv[1]
    tl = float(sys.argv[2])
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 7
    sys.path.insert(0, str(REPO / SOLVER))
    os.environ["OGC_SEED"] = str(seed)
    import myalgorithm as ma
    from ortools.sat.python import cp_model as cp
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    B, bays, W = prob["blocks"], prob["bays"], prob["weights"]
    n, m = len(B), len(bays)
    areas = [b["width"] * b["height"] for b in bays]
    u = [sum(areas) / m / a for a in areas]
    wl = [b["workload"] for b in B]
    PREF = [[float(v) for v in b["bay_preferences"]] for b in B]
    SMAX = [max(p) for p in PREF]
    SC = 1000
    uw = [int(round(SC * x)) for x in u]

    sol = ma.algorithm(prob, timelimit=tl)
    eng, D, P = ma._fresh(prob, 4)
    st = [0] * (n * 7)
    for day, ops in sol["operations"].items():
        for op in ops:
            if op["type"] == "ENTRY":
                i = op["block_id"]
                st[i * 7:i * 7 + 7] = [op["bay_id"], op["orient_idx"], op["x"],
                                       op["y"], int(day), int(day) + P[i], 1]
    eng.set_solution(st)
    eng.build_occ(eng.horizon())
    base = eng.objective()
    print(f"{prob_id}: driver board {base[3]:,.0f}  Z1={base[0]:.0f}"
          f"   (TL {tl:.0f}s, seed {seed})")

    # parents: the driver's final board + the portfolio's runner-up thread
    # boards, which the run computed and then discarded.
    parents = [list(st)]
    for flat in eng.get_candidates():
        if list(flat) != parents[0]:
            parents.append(list(flat))
    parents = parents[:4]
    for k, f in enumerate(parents[1:], 1):
        eng.set_solution(f)
        eng.build_occ(eng.horizon())
        o = eng.objective()
        print(f"   parent {k}: {o[3]:,.0f}  Z1={o[0]:.0f}")
    if len(parents) < 2:
        print("   no runner-up candidates available -- nothing to recombine")
        return

    # per-block candidate set = its placement in each parent (deduped)
    CAND, flat, idx = {}, [], []
    for i in range(n):
        cs = []
        for f in parents:
            if f[i * 7 + 6]:
                c = (f[i * 7], f[i * 7 + 1], f[i * 7 + 2], f[i * 7 + 3],
                     f[i * 7 + 4])
                if c not in cs:
                    cs.append(c)
        if cs:
            CAND[i] = cs
            for k, c in enumerate(cs):
                flat += [i, c[0], c[1], c[2], c[3], c[4]]
                idx.append((i, k))
    t0 = time.time()
    prs = eng.pair_conflicts(flat)
    print(f"   {len(idx):,} candidates over {len(parents)} parents,"
          f" {len(prs)//2:,} conflict pairs   [{time.time()-t0:.0f}s]")

    mdl = cp.CpModel()
    V = {}
    for i in CAND:
        V[i] = [mdl.NewBoolVar(f"z{i}_{k}") for k in range(len(CAND[i]))]
        mdl.AddExactlyOne(V[i])
        mdl.AddHint(V[i][0], 1)              # parent 0 = the driver's board
    for q in range(0, len(prs), 2):
        (a1, k1), (a2, k2) = idx[prs[q]], idx[prs[q + 1]]
        mdl.AddBoolOr([V[a1][k1].Not(), V[a2][k2].Not()])
    # true objective over the selection
    terms = []
    loadv = []
    tot = sum(wl)
    for j in range(m):
        v = mdl.NewIntVar(0, uw[j] * tot, f"v{j}")
        mdl.Add(v == sum(uw[j] * int(wl[i])
                         * sum(V[i][k] for k, c in enumerate(CAND[i])
                               if c[0] == j)
                         for i in CAND))
        loadv.append(v)
    hi = mdl.NewIntVar(0, max(uw) * tot, "hi")
    lo = mdl.NewIntVar(0, max(uw) * tot, "lo")
    mdl.AddMaxEquality(hi, loadv)
    mdl.AddMinEquality(lo, loadv)
    spread = mdl.NewIntVar(0, max(uw) * tot, "sp")
    mdl.Add(spread == hi - lo)
    for i in CAND:
        for k, c in enumerate(CAND[i]):
            tardy = max(0, c[4] + P[i] - D[i])
            reg = int(round(SMAX[i] - PREF[i][c[0]]))
            terms.append((W["w1"] * SC * tardy + W["w3"] * SC * reg) * V[i][k])
    mdl.Minimize(sum(terms) + W["w2"] * spread)
    sv = cp.CpSolver()
    sv.parameters.max_time_in_seconds = 120.0
    sv.parameters.num_search_workers = 8
    t1 = time.time()
    stt = sv.Solve(mdl)
    nm = sv.StatusName(stt)
    if stt not in (cp.OPTIMAL, cp.FEASIBLE):
        print(f"   recombination: {nm}   [{time.time()-t1:.0f}s]")
        return
    new = [0] * (n * 7)
    for i in CAND:
        for k, c in enumerate(CAND[i]):
            if sv.Value(V[i][k]):
                new[i * 7:i * 7 + 7] = [c[0], c[1], c[2], c[3], c[4],
                                        c[4] + P[i], 1]
                break
    eng.set_solution(new)
    eng.build_occ(eng.horizon())
    bad = sum(1 for i in range(n) if new[i * 7 + 6] and not eng.vp_ok(i))
    o = eng.objective()
    sol2 = ma._recs_to_sol(ma._extract(eng, n), eng.entry_ranks())
    res = ma._check(prob, sol2)
    feas = res.get("feasible") if res else None
    print(f"   recombination ({nm}, {time.time()-t1:.0f}s): {o[3]:,.0f}"
          f"  Z1={o[0]:.0f}  vp violations {bad}  checker feasible={feas}")
    print(f"   vs driver board {(o[3]-base[3])/base[3]*100:+.2f}%")


if __name__ == "__main__":
    main()
