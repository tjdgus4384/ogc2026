#!/usr/bin/env python3
"""Exact recombination of INDEPENDENT boards -- the operation no neighbourhood can do.

08-13/14 reduced the gap to one shape: a better board differs from ours by a few
blocks' timing plus many repositions that merely accommodate them, at identical
density. Small neighbourhoods cannot see the decisive few; large ones return to
the same arrangement (0/400 local, 0/20 exact CP, 0/3,680 windows). Only a full
restart reaches it, because rebuilding all n blocks makes accommodation free.

Recombination is the one operator that can take BOTH at once: independent
restarts each carry an internally consistent accommodation, so choosing per
block which parent's placement to keep can combine one parent's decisive timing
with another's accommodating layout. A neighbourhood search cannot express that
-- it has only one board to perturb.

`recomb.py` already measured this as "= best parent" and was closed, but its
parents were the four thread boards of a SINGLE portfolio run, which share a
restart. Independent draws agree on only 13-33% of bay assignments (the plateau
note); same-run threads agree far more. Independent parents have never been
tried.

Model: one boolean per (block, parent placement), exactly-one per block,
pairwise incompatibility from the kernel's own `pair_conflicts`, true objective
(w1 tardiness + w2 bay-load spread + w3 preference regret) minimised by CP-SAT.
Uses cached boards, so it costs no solver time.

    python3 tools/xrecomb.py prob_5 600 7 11 13 17
"""
import json
import os
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent


def main():
    prob_id = sys.argv[1]
    tl = float(sys.argv[2])
    seeds = [int(x) for x in sys.argv[3:]] or [7, 11, 13, 17]
    sys.path.insert(0, str(REPO / "tools"))
    from board import get_board
    from ortools.sat.python import cp_model as cp

    # every parent needs its own process-level seed, so load them via the cache
    # with OGC_SEED already set by the caller for the FIRST one; the rest come
    # straight off disk and never touch _PSEED.
    base0, D, P, eng, hz, prob = get_board(prob_id, tl, seeds[0])
    n = len(prob["blocks"])
    parents = [list(base0)]
    for s in seeds[1:]:
        f = REPO / "runs" / "boards"
        hit = [p for p in f.glob(f"{prob_id}_tl{int(tl)}_s{s}_*.json")]
        if not hit:
            print(f"   seed {s}: no cached board -- run tools/board.py warm first")
            continue
        parents.append(json.loads(hit[0].read_text())["base"])
    if len(parents) < 2:
        sys.exit("need at least two parents")

    B, bays, W = prob["blocks"], prob["bays"], prob["weights"]
    m = len(bays)
    areas = [b["width"] * b["height"] for b in bays]
    u = [sum(areas) / m / a for a in areas]
    wl = [b["workload"] for b in B]
    PREF = [[float(v) for v in b["bay_preferences"]] for b in B]
    SMAX = [max(p) for p in PREF]
    SC = 1000
    uw = [int(round(SC * x)) for x in u]

    objs = []
    for f in parents:
        eng.set_solution(f)
        eng.build_occ(hz)
        objs.append(eng.objective()[3])
    print(f"{prob_id}: {len(parents)} independent parents (seeds {seeds})")
    for s, o in zip(seeds, objs):
        print(f"   s{s:<4d} {o:>14,.0f}")
    best_parent = min(objs)

    # per-block candidate = its placement in each parent, deduped
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
    # The shipped pairwise model OVER-REPORTS: feeding parent 0's own
    # placements alone returns 3 conflicts on P4, yet that board is a real
    # checker-feasible solution. (builds/pc2 carries the corrected exit
    # tie-break, which is why recomb.py pinned itself to that kernel.) Rather
    # than depend on a shelved build, drop any pair whose two candidates
    # coexist in the SAME parent -- a parent is feasible by construction, so
    # its own pairs are compatible by definition. Without this the model is
    # INFEASIBLE even though every parent board is a valid selection.
    coexist = set()
    for f in parents:
        pl = [i for i in range(n) if f[i * 7 + 6]]
        key = {i: (f[i * 7], f[i * 7 + 1], f[i * 7 + 2], f[i * 7 + 3],
                   f[i * 7 + 4]) for i in pl}
        for a in range(len(pl)):
            for b in range(a + 1, len(pl)):
                coexist.add((pl[a], key[pl[a]], pl[b], key[pl[b]]))
    keep = []
    drop = 0
    for q in range(0, len(prs), 2):
        (a1, k1), (a2, k2) = idx[prs[q]], idx[prs[q + 1]]
        c1, c2 = CAND[a1][k1], CAND[a2][k2]
        pair = (a1, c1, a2, c2) if a1 < a2 else (a2, c2, a1, c1)
        if pair in coexist:
            drop += 1
            continue
        keep += [prs[q], prs[q + 1]]
    prs = keep
    if drop:
        print(f"   dropped {drop:,} spurious conflicts (pairs that coexist"
              f" inside a parent)")
    agree = sum(1 for i in CAND if len(CAND[i]) == 1) / max(1, len(CAND))
    print(f"   {len(idx):,} candidates, {len(prs)//2:,} conflict pairs,"
          f" parents agree on {agree*100:.0f}% of blocks  [{time.time()-t0:.0f}s]")

    mdl = cp.CpModel()
    V = {i: [mdl.NewBoolVar(f"z{i}_{k}") for k in range(len(CAND[i]))]
         for i in CAND}
    for i in CAND:
        mdl.AddExactlyOne(V[i])
        mdl.AddHint(V[i][0], 1)
    for q in range(0, len(prs), 2):
        (a1, k1), (a2, k2) = idx[prs[q]], idx[prs[q + 1]]
        mdl.AddBoolOr([V[a1][k1].Not(), V[a2][k2].Not()])
    D_ = [int(round(b["due_date"])) for b in B]
    P_ = [max(1, int(-(-b["processing_time"] // 1))) for b in B]
    loadv = []
    tot = sum(wl)
    for j in range(m):
        v = mdl.NewIntVar(0, uw[j] * int(tot) + 1, f"v{j}")
        mdl.Add(v == sum(uw[j] * int(wl[i])
                         * sum(V[i][k] for k, c in enumerate(CAND[i])
                               if c[0] == j) for i in CAND))
        loadv.append(v)
    hi = mdl.NewIntVar(0, max(uw) * int(tot) + 1, "hi")
    lo = mdl.NewIntVar(0, max(uw) * int(tot) + 1, "lo")
    mdl.AddMaxEquality(hi, loadv)
    mdl.AddMinEquality(lo, loadv)
    spread = mdl.NewIntVar(0, max(uw) * int(tot) + 1, "sp")
    mdl.Add(spread == hi - lo)
    terms = []
    for i in CAND:
        for k, c in enumerate(CAND[i]):
            tardy = max(0, c[4] + P_[i] - D_[i])
            reg = int(round(SMAX[i] - PREF[i][c[0]]))
            terms.append((int(W["w1"]) * SC * tardy
                          + int(W["w3"]) * SC * reg) * V[i][k])
    mdl.Minimize(sum(terms) + int(W["w2"]) * spread)
    sv = cp.CpSolver()
    sv.parameters.max_time_in_seconds = 240.0
    sv.parameters.num_search_workers = 8
    t1 = time.time()
    stt = sv.Solve(mdl)
    nm = sv.StatusName(stt)
    if stt not in (cp.OPTIMAL, cp.FEASIBLE):
        print(f"   recombination: {nm}  [{time.time()-t1:.0f}s]")
        return
    new = [0] * (n * 7)
    for i in CAND:
        for k, c in enumerate(CAND[i]):
            if sv.Value(V[i][k]):
                new[i * 7:i * 7 + 7] = [c[0], c[1], c[2], c[3], c[4],
                                        c[4] + P_[i], 1]
                break
    eng.set_solution(new)
    eng.build_occ(hz)
    o = eng.objective()
    sys.path.insert(0, str(REPO / "solver"))
    import myalgorithm as ma
    res = ma._check(prob, ma._recs_to_sol(ma._extract(eng, n), eng.entry_ranks()))
    print(f"   recombined ({nm}, {time.time()-t1:.0f}s): {o[3]:,.0f}"
          f"  checker={res.get('feasible') if res else None}")
    print(f"   vs best parent {best_parent:,.0f}: "
          f"{(o[3]-best_parent)/best_parent*100:+.2f}%")


if __name__ == "__main__":
    main()
