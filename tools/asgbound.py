#!/usr/bin/env python3
"""How much of the objective is reachable by re-assigning bays alone?

zsplit found the campaign has been aiming at the wrong term on P3: its z1 is
EXACTLY 0 (0 of 200 tardy), so 100% of its objective is bay imbalance (19.1%)
plus preference regret (80.9%) -- the two terms no lever this campaign ever
targeted. P4 carries 19.4% in regret too. And P3 is our closest board to the
target (+11.0%).

Both terms depend on the bay assignment ALONE, with no geometry and no time. So
their joint optimum over all assignments is computable exactly, and since
w1*z1 >= 0 it is a genuine LOWER BOUND on the whole objective:

    total >= min_assignment ( w2 * spread + w3 * regret )

That turns "are we close on P3?" from a guess into a number. If our board is
near the bound, P3 is finished and the remaining gap is elsewhere. If the bound
is far below, the gap is an assignment problem -- a far easier object than the
packing the wall is made of.

The bound ignores packing feasibility, so it is optimistic; that is what makes
it a bound. Every formula is verified against the kernel's own z2/z3 on the
shipped board before the bound is trusted.

    python3 tools/asgbound.py
"""
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
from board import get_board
from ortools.sat.python import cp_model as cp

SC = 1000

for pid, tl in (("prob_3", 240), ("prob_4", 480), ("prob_5", 600),
                ("prob_6", 900)):
    base, D, P, eng, hz, prob = get_board(pid, tl, 7)
    eng.set_solution(base)
    eng.build_occ(hz)
    z1, z2, z3, tot = eng.objective()
    B, bays, W = prob["blocks"], prob["bays"], prob["weights"]
    n, m = len(B), len(bays)
    areas = [b["width"] * b["height"] for b in bays]
    u = [sum(areas) / m / a for a in areas]
    wl = [b["workload"] for b in B]
    PREF = [[float(v) for v in b["bay_preferences"]] for b in B]
    SMAX = [max(p) for p in PREF]
    asg = [base[i * 7] for i in range(n)]
    placed = [bool(base[i * 7 + 6]) for i in range(n)]

    load = [sum(u[j] * wl[i] for i in range(n) if placed[i] and asg[i] == j)
            for j in range(m)]
    myz2 = max(load) - min(load)
    myz3 = sum(SMAX[i] - PREF[i][asg[i]] for i in range(n) if placed[i])
    ok2 = abs(myz2 - z2) < max(1.0, 0.01 * abs(z2))
    ok3 = abs(myz3 - z3) < max(1.0, 0.01 * abs(z3))
    print(f"{pid}: total {tot:,.0f}   z2 {z2:,.1f} (mine {myz2:,.1f}"
          f" {'ok' if ok2 else 'MISMATCH'})   z3 {z3:,.0f} (mine {myz3:,.0f}"
          f" {'ok' if ok3 else 'MISMATCH'})", flush=True)
    if not (ok2 and ok3):
        print("   formula does not reproduce the kernel -- bound not trusted",
              flush=True)
        continue

    uw = [int(round(SC * x)) for x in u]
    mdl = cp.CpModel()
    V = [[mdl.NewBoolVar(f"x{i}_{j}") for j in range(m)] for i in range(n)]
    for i in range(n):
        mdl.AddExactlyOne(V[i])
        mdl.AddHint(V[i][asg[i]], 1)
    cap = sum(uw) * int(sum(wl)) + 1
    lv = []
    for j in range(m):
        v = mdl.NewIntVar(0, cap, f"L{j}")
        mdl.Add(v == sum(uw[j] * int(wl[i]) * V[i][j] for i in range(n)))
        lv.append(v)
    hi, lo = mdl.NewIntVar(0, cap, "hi"), mdl.NewIntVar(0, cap, "lo")
    mdl.AddMaxEquality(hi, lv)
    mdl.AddMinEquality(lo, lv)
    reg = sum(int(round(SC * (SMAX[i] - PREF[i][j]))) * V[i][j]
              for i in range(n) for j in range(m))
    mdl.Minimize(int(W["w2"]) * (hi - lo) + int(W["w3"]) * reg)
    sv = cp.CpSolver()
    sv.parameters.max_time_in_seconds = 300.0
    sv.parameters.num_search_workers = 8
    st = sv.Solve(mdl)
    nm = sv.StatusName(st)
    if st not in (cp.OPTIMAL, cp.FEASIBLE):
        print(f"   assignment bound: {nm}", flush=True)
        continue
    lb = sv.BestObjectiveBound() / SC
    val = sv.ObjectiveValue() / SC
    cur = W["w2"] * z2 + W["w3"] * z3
    print(f"   assignment-only optimum {val:,.0f} ({nm}, proven >= {lb:,.0f})"
          f"   vs ours {cur:,.0f}   headroom {(val-cur)/tot*100:+.1f}% of total",
          flush=True)
    print(f"   => lower bound on {pid} total: {lb:,.0f}"
          f"  (we are {tot:,.0f}, i.e. {(tot-lb)/max(1,lb)*100:+.1f}%)",
          flush=True)
