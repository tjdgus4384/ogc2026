#!/usr/bin/env python3
"""Compose the per-bay repacks into one board, and judge it officially.

repack.py opened the elimination table's last cell on P4: repacking a bay's
geometry from scratch -- residents keeping their entry days -- seats tardy
blocks punctually (bay 0: 2 blocks/24 days, bay 1: 1/3, bay 2: 4/27; summed
719,982 objective = 75% of the gap to the all-team minimum). Those numbers
are per-bay optimistic: the same tardy block can be seated by several bays'
solutions, and nothing was realized or checker-judged.

This composes properly. Bays are solved sequentially, richest first; each
bay's CP sees only the tardy blocks no earlier bay has already seated; every
seated block is REMOVED from its original bay before that bay is solved
(removal only relaxes crane constraints, so the remaining residents' witness
anchors stay valid); and the final board is scored on the engine, vp-checked
block by block, and judged by the official checker. The output is a board and
its verified objective, not a promise.

    python3 tools/repack2.py prob_4 TL [seed]
"""
import json
import math
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
    B, bays = prob["blocks"], prob["bays"]
    n, m = len(B), len(bays)

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
    R = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]
    print(f"{prob_id}: board {base[3]:,.0f}  Z1={base[0]:.0f}"
          f"   (TL {tl:.0f}s, seed {seed})")

    new = list(st)                       # the composed board, edited in place
    tardy_pool = [i for i in range(n) if st[i * 7 + 6] and st[i * 7 + 5] > D[i]
                  and R[i] <= D[i] - P[i]]
    seated = {}                          # i -> (bay, ori, x, y, e)

    def grid(i, j, day, lim):
        W_, H_ = bays[j]["width"], bays[j]["height"]
        cs = []
        for oi, sh in enumerate(B[i]["shape"]):
            xs = [v[0] for lay in sh["layers"] for v in lay]
            ys = [v[1] for lay in sh["layers"] for v in lay]
            xlo = int(math.ceil(-min(xs) - 1e-9))
            ylo = int(math.ceil(-min(ys) - 1e-9))
            xhi = int(math.floor(W_ - max(xs) + 1e-9))
            yhi = int(math.floor(H_ - max(ys) + 1e-9))
            for x in range(xlo, xhi + 1, 2):
                for y in range(ylo, yhi + 1, 2):
                    cs.append((j, oi, x, y, day))
        if len(cs) > lim:
            step = len(cs) / lim
            cs = [cs[int(q * step)] for q in range(lim)]
        return cs

    for j in (2, 0, 1):                  # richest first, measured by repack.py
        mine = [i for i in range(n) if new[i * 7 + 6] and new[i * 7] == j
                and i not in seated]
        pool = [i for i in tardy_pool if i not in seated and new[i * 7] != j]
        CAND, flat, idx, save = {}, [], [], {}
        for i in mine:
            cs = grid(i, j, new[i * 7 + 4], 60)
            wit = (j, new[i * 7 + 1], new[i * 7 + 2], new[i * 7 + 3],
                   new[i * 7 + 4])
            if wit not in cs:
                cs.append(wit)
            CAND[i] = cs
        for i in pool:
            cs = grid(i, j, D[i] - P[i], 40)
            if cs:
                CAND[i] = cs
                save[i] = new[i * 7 + 5] - D[i]
        for i in CAND:
            for k, c in enumerate(CAND[i]):
                flat += [i, c[0], c[1], c[2], c[3], c[4]]
                idx.append((i, k))
        prs = eng.pair_conflicts(flat)
        mdl = cp.CpModel()
        V, use = {}, {}
        for i in CAND:
            V[i] = [mdl.NewBoolVar(f"z{i}_{k}") for k in range(len(CAND[i]))]
            if i in save:
                use[i] = mdl.NewBoolVar(f"u{i}")
                mdl.Add(sum(V[i]) == use[i])
            else:
                mdl.AddExactlyOne(V[i])
                mdl.AddHint(V[i][len(CAND[i]) - 1], 1)
        for q in range(0, len(prs), 2):
            (a1, k1), (a2, k2) = idx[prs[q]], idx[prs[q + 1]]
            mdl.AddBoolOr([V[a1][k1].Not(), V[a2][k2].Not()])
        mdl.Maximize(sum(save[i] * use[i] for i in save))
        sv = cp.CpSolver()
        sv.parameters.max_time_in_seconds = 180.0
        sv.parameters.num_search_workers = 8
        t0 = time.time()
        stt = sv.Solve(mdl)
        nm = sv.StatusName(stt)
        if stt not in (cp.OPTIMAL, cp.FEASIBLE):
            print(f"   bay {j}: {nm} -- left untouched   [{time.time()-t0:.0f}s]")
            continue
        got = [i for i in save if sv.Value(use[i])]
        # commit: residents' new geometry, extras' relocation
        for i in CAND:
            if i in save and i not in got:
                continue
            for k, c in enumerate(CAND[i]):
                if sv.Value(V[i][k]):
                    e = c[4]
                    new[i * 7:i * 7 + 7] = [c[0], c[1], c[2], c[3], e,
                                            e + P[i], 1]
                    if i in save:
                        seated[i] = c
                    break
        print(f"   bay {j}: {nm}, +{len(got)} punctual"
              f" ({sum(save[i] for i in got)} days)   [{time.time()-t0:.0f}s]")

    eng.set_solution(new)
    eng.build_occ(eng.horizon())
    bad = sum(1 for i in range(n) if new[i * 7 + 6] and not eng.vp_ok(i))
    o = eng.objective()
    sol2 = ma._recs_to_sol(ma._extract(eng, n), eng.entry_ranks())
    res = ma._check(prob, sol2)
    feas = res.get("feasible") if res else None
    print(f"   composed: {o[3]:,.0f}  Z1={o[0]:.0f} Z2={o[1]:.0f} Z3={o[2]:.0f}"
          f"   vp violations {bad}, checker feasible={feas}")
    print(f"   vs board {(o[3]-base[3])/base[3]*100:+.2f}%"
          f"   ({len(seated)} blocks relocated punctually)")


if __name__ == "__main__":
    main()
