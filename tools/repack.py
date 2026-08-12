#!/usr/bin/env python3
"""Set growth WITH geometric freedom: the question P4 never got asked.

The elimination table has one untried cell. bayscratch re-packed a bay's
geometry with the SAME block set (optimal, exactly). overfill grew the set
with residents PINNED at our positions (+1 of 12). Nobody has asked the union:
repack the bay's geometry from scratch AND offer the tardy blocks as punctual
additions. blocked.py says 83% of P4's tardy days are fragmentation -- no free
bbox anywhere at the pinned positions -- which is precisely the condition
under which repacking-with-growth could differ from both.

Per bay: residents keep their own entry days (the schedule axis is closed;
only geometry moves) and MUST seat -- their candidate sets carry their witness
anchors, so residents-SAT is guaranteed and any infeasibility is an encoding
bug, per the instrument-validation discipline. Tardy blocks whose punctual
window allows this bay are OPTIONAL at entry D-P (their last punctual day),
grid candidates only. CP maximises the tardiness saved by the seated extras.

    extras seat   per-bay packing is NOT optimal once the set may grow, and
                  the exact machinery has a real P4 target;
    none seat     the last cell of the table closes, and P4's gap is beyond
                  every decomposed move this solver's vocabulary can express.

    python3 tools/repack.py prob_4 BAY TL [seed]
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
    bay = int(sys.argv[2])
    tl = float(sys.argv[3])
    seed = int(sys.argv[4]) if len(sys.argv) > 4 else 7
    sys.path.insert(0, str(REPO / SOLVER))
    os.environ["OGC_SEED"] = str(seed)
    import myalgorithm as ma
    from ortools.sat.python import cp_model as cp
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    B, bays = prob["blocks"], prob["bays"]
    n, m = len(B), len(bays)
    W_, H_ = bays[bay]["width"], bays[bay]["height"]

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
    mine = [i for i in range(n) if st[i * 7 + 6] and st[i * 7] == bay]
    tardy = [i for i in range(n) if st[i * 7 + 6] and st[i * 7 + 5] > D[i]
             and R[i] <= D[i] - P[i]]
    print(f"{prob_id} bay {bay}: board {base[3]:,.0f}, {len(mine)} residents,"
          f" {len(tardy)} tardy candidates   (TL {tl:.0f}s, seed {seed})")

    def grid(i, day, lim):
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
                    cs.append((bay, oi, x, y, day))
        if len(cs) > lim:
            step = len(cs) / lim
            cs = [cs[int(q * step)] for q in range(lim)]
        return cs

    CAND, flat, idx = {}, [], []
    for i in mine:
        cs = grid(i, st[i * 7 + 4], 60)
        wit = (bay, st[i * 7 + 1], st[i * 7 + 2], st[i * 7 + 3], st[i * 7 + 4])
        if wit not in cs:
            cs.append(wit)
        CAND[i] = cs
    save = {}
    for i in tardy:
        d = D[i] - P[i]                      # last punctual day, this bay
        cs = grid(i, d, 40)
        if not cs:
            continue
        CAND[i] = cs
        save[i] = st[i * 7 + 5] - D[i]       # tardy days its seating erases
    for i in CAND:
        for k, c in enumerate(CAND[i]):
            flat += [i, c[0], c[1], c[2], c[3], c[4]]
            idx.append((i, k))
    t0 = time.time()
    prs = eng.pair_conflicts(flat)
    print(f"   candidates {len(idx):,} ({len(mine)} anchored residents,"
          f" {len(save)} optional tardy)   {len(prs)//2:,} conflict pairs"
          f"   [{time.time()-t0:.0f}s]")

    mdl = cp.CpModel()
    V, use = {}, {}
    for i in CAND:
        V[i] = [mdl.NewBoolVar(f"z{i}_{k}") for k in range(len(CAND[i]))]
        if i in save:
            use[i] = mdl.NewBoolVar(f"u{i}")
            mdl.Add(sum(V[i]) == use[i])
        else:
            mdl.AddExactlyOne(V[i])
            mdl.AddHint(V[i][len(CAND[i]) - 1], 1)     # witness anchor
    for q in range(0, len(prs), 2):
        (a1, k1), (a2, k2) = idx[prs[q]], idx[prs[q + 1]]
        mdl.AddBoolOr([V[a1][k1].Not(), V[a2][k2].Not()])
    mdl.Maximize(sum(save[i] * use[i] for i in save))
    sv = cp.CpSolver()
    sv.parameters.max_time_in_seconds = 240.0
    sv.parameters.num_search_workers = 8
    t1 = time.time()
    stt = sv.Solve(mdl)
    nm = sv.StatusName(stt)
    if stt not in (cp.OPTIMAL, cp.FEASIBLE):
        print(f"   {nm} [{time.time()-t1:.0f}s]"
              "   <- residents alone unseatable = ENCODING BUG (witness existed)")
        return
    got = [i for i in save if sv.Value(use[i])]
    days = sum(save[i] for i in got)
    print(f"   {nm} in {time.time()-t1:.0f}s: seats {len(got)} of {len(save)}"
          f" tardy blocks punctually, erasing {days} tardy days"
          f" (= {days * prob['weights']['w1']:,} objective)"
          + ("   <- THE CELL OPENS" if got else "   <- the last cell closes"))


if __name__ == "__main__":
    main()
