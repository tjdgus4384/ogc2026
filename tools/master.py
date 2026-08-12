#!/usr/bin/env python3
"""The rho* experiment: is the gap scheduling, or density?

The campaign's evidence, in one chain. No block is individually forced late
(Z1_floor = 0), yet Z1 is 170-4,072 -- so tardiness is pure contention, a
question of WHO yields. The winners carry large Z1 too (P4 95 vs our 174), so
they do not eliminate the sacrifice; they choose it better. Our packing of the
set we pick is provably optimal (bayscratch: CP equals our value with the bound
tight) and provably full (overfill: +1 of 12 / +0 addable at the hottest
(bay, day)). And the pipeline never optimises the choice: insertGreedy gives
every block its own earliest feasible day -- sequential selfish insertion --
and 86% of the compute polishes geometry, which the objective cannot see.
Nothing anywhere can say "delay A three days so B, C and D save ten".

So the one question that decides the direction: at the packing density we
DEMONSTRABLY achieve (per-bay realized peaks ~0.65-0.68), does a better
(bay, entry) schedule reach the winner's objective?

This solves that schedule exactly, with packing relaxed to area:

    master: choose (bay, entry) per block
            per-bay Cumulative(demand = footprint area, capacity = rho*A_j)
            minimise the TRUE objective w1*Z1 + w2*Z2 + w3*Z3, no proxy

sweeping rho. Reading the curve:

    rho* <= 0.67   scheduling alone reaches the winner at our own density
                   -> build the master + locked realization
    rho* >= 0.75   the winners must pack denser than we ever have
                   -> the axis is geometric density, not scheduling
    in between     both, in that order

Cumulative is a relaxation of 2D packing, so each objective value is a LOWER
bound at that density: if even the relaxation cannot reach the target at
rho = 0.75, no schedule at that density can.

    python3 tools/master.py prob_4 0.55,0.60,0.65,0.70,0.75,0.80,0.90,1.00
"""
import json
import math
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
SC = 1000
TGT = {"prob_3": 64695, "prob_4": 1908043, "prob_5": 7718486, "prob_6": 25452695}
OURS = {"prob_3": 78710, "prob_4": 3021623, "prob_5": 10620093, "prob_6": 28851794}


def poly_area(v):
    s = 0.0
    for k in range(len(v)):
        x1, y1 = v[k]
        x2, y2 = v[(k + 1) % len(v)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2


def main():
    prob_id = sys.argv[1]
    rhos = [float(x) for x in sys.argv[2].split(",")]
    from ortools.sat.python import cp_model as cp
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    B, bays, W = prob["blocks"], prob["bays"], prob["weights"]
    n, m = len(B), len(bays)
    A = [b["width"] * b["height"] for b in bays]
    u = [sum(A) / m / a for a in A]
    uw = [int(round(SC * x)) for x in u]
    wl = [b["workload"] for b in B]
    R = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]
    D = [int(round(b["due_date"])) for b in B]
    P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
    PREF = [[float(v) for v in b["bay_preferences"]] for b in B]
    REG = [[int(round(max(PREF[i]) - PREF[i][j])) for j in range(m)]
           for i in range(n)]
    # demand: the block's most favourable orientation (the packer may choose),
    # footprint = the layer union the crane sterilises -- consistent with how
    # saturation.py measured our own realized densities.
    AR = [int(round(min(max(poly_area(l) for l in sh["layers"])
                        for sh in b["shape"]))) for b in B]
    H = max(D) + 64
    print(f"{prob_id}: n={n} m={m}  target {TGT[prob_id]:,}  our median {OURS[prob_id]:,}"
          f"   demands sum {sum(AR):,} vs yard {sum(A):,}")

    for rho in rhos:
        t0 = time.time()
        mdl = cp.CpModel()
        x = [[mdl.NewBoolVar(f"x{i}_{j}") for j in range(m)] for i in range(n)]
        s = [mdl.NewIntVar(R[i], H - P[i], f"s{i}") for i in range(n)]
        e = [mdl.NewIntVar(R[i], H, f"e{i}") for i in range(n)]
        t = [mdl.NewIntVar(0, H, f"t{i}") for i in range(n)]
        for i in range(n):
            mdl.AddExactlyOne(x[i])
            mdl.Add(e[i] == s[i] + P[i])
            mdl.Add(t[i] >= e[i] - D[i])
        for j in range(m):
            iv, dm = [], []
            for i in range(n):
                iv.append(mdl.NewOptionalIntervalVar(s[i], P[i], e[i],
                                                     x[i][j], f"iv{i}_{j}"))
                dm.append(AR[i])
            mdl.AddCumulative(iv, dm, int(rho * A[j]))
        tot = sum(wl)
        V = []
        for j in range(m):
            v = mdl.NewIntVar(0, uw[j] * tot, f"v{j}")
            mdl.Add(v == sum(uw[j] * wl[i] * x[i][j] for i in range(n)))
            V.append(v)
        hi = mdl.NewIntVar(0, max(uw) * tot, "hi")
        lo = mdl.NewIntVar(0, max(uw) * tot, "lo")
        mdl.AddMaxEquality(hi, V)
        mdl.AddMinEquality(lo, V)
        spread = mdl.NewIntVar(0, max(uw) * tot, "sp")
        mdl.Add(spread == hi - lo)
        z3 = mdl.NewIntVar(0, sum(max(r) for r in REG), "z3")
        mdl.Add(z3 == sum(REG[i][j] * x[i][j] for i in range(n) for j in range(m)))
        z1 = mdl.NewIntVar(0, n * H, "z1")
        mdl.Add(z1 == sum(t))
        mdl.Minimize(W["w1"] * SC * z1 + W["w2"] * spread + W["w3"] * SC * z3)
        sv = cp.CpSolver()
        sv.parameters.max_time_in_seconds = 240.0
        sv.parameters.num_search_workers = 8
        st = sv.Solve(mdl)
        nm = sv.StatusName(st)
        if st not in (cp.OPTIMAL, cp.FEASIBLE):
            print(f"   rho {rho:.2f}  {nm}   [{time.time()-t0:.0f}s]")
            continue
        obj = sv.ObjectiveValue() / SC
        bnd = sv.BestObjectiveBound() / SC
        mark = "  <<== BEATS TARGET" if obj <= TGT[prob_id] else ""
        print(f"   rho {rho:.2f}  {nm:<8} obj {obj:>12,.0f} (bound {bnd:>12,.0f})"
              f"  Z1={sv.Value(z1):>5d} Z2={sv.Value(spread)/SC:>7,.0f}"
              f" Z3={sv.Value(z3):>5d}   vs target {TGT[prob_id]:,}"
              f"   [{time.time()-t0:.0f}s]{mark}")


if __name__ == "__main__":
    main()
