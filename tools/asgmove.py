#!/usr/bin/env python3
"""Can the assignment headroom be captured, and how far can packing follow?

asgbound found the largest actionable number in this campaign. z2 and z3 depend
on the bay assignment alone, and their joint optimum -- proven OPTIMAL by CP-SAT
in seconds -- sits far below what we ship:

    P3  ours 79,730  assignment optimum 36,759   -53.9% of total (z1 is 0 there)
    P4                                           -16.3% of total
    P5  -6.3%   P6  -3.5%

The driver never solves this. On `spec` boards it locks the top 40% of blocks by
preference SPREAD to their argmax bay (myalgorithm.py:715) -- a heuristic that
ignores the z2/z3 trade entirely and leaves 60% of the blocks unconsidered.

The optimum is not free: it ignores packing. So sweep a MOVE BUDGET k -- the
number of blocks allowed to leave their current bay -- and watch where packing
stops following. That gives the exchange curve rather than one yes/no.

Arms are paired and both fully locked, so only the assignment differs (a free
control would measure the lock, not the assignment -- instrument defect 5):

    A   lock = the shipped board's own assignment
    B_k lock = CP-SAT optimum within k moves of it

    python3 tools/asgmove.py prob_3 240 120
"""
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
from board import get_board
from ortools.sat.python import cp_model as cp

SC = 1000


def main():
    pid = sys.argv[1]
    tl = float(sys.argv[2])
    T = float(sys.argv[3]) if len(sys.argv) > 3 else 120.0
    base, D, P, eng, hz, prob = get_board(pid, tl, 7)
    sys.path.insert(0, str(REPO / "solver"))
    import myalgorithm as ma
    B, bays, W = prob["blocks"], prob["bays"], prob["weights"]
    n, m = len(B), len(bays)
    areas = [b["width"] * b["height"] for b in bays]
    u = [sum(areas) / m / a for a in areas]
    uw = [int(round(SC * x)) for x in u]
    wl = [b["workload"] for b in B]
    PREF = [[float(v) for v in b["bay_preferences"]] for b in B]
    SMAX = [max(p) for p in PREF]
    cur = [base[i * 7] for i in range(n)]
    eng.set_solution(base)
    eng.build_occ(hz)
    z1, z2, z3, tot = eng.objective()
    print(f"{pid}: shipped {tot:,.0f}  (z1 {z1:,.0f} z2 {z2:,.1f} z3 {z3:,.0f})",
          flush=True)

    def solve(k):
        mdl = cp.CpModel()
        V = [[mdl.NewBoolVar(f"x{i}_{j}") for j in range(m)] for i in range(n)]
        for i in range(n):
            mdl.AddExactlyOne(V[i])
            mdl.AddHint(V[i][cur[i]], 1)
        if k is not None:
            mdl.Add(sum(V[i][cur[i]] for i in range(n)) >= n - k)
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
        sv.parameters.max_time_in_seconds = 60.0
        sv.parameters.num_search_workers = 8
        st = sv.Solve(mdl)
        if st not in (cp.OPTIMAL, cp.FEASIBLE):
            return None, None
        a = [next(j for j in range(m) if sv.Value(V[i][j])) for i in range(n)]
        return a, sv.ObjectiveValue() / SC

    def run(asg, tag):
        """lock the assignment, rebuild, anneal, report."""
        eng.set_assignment_lock(list(asg))
        bad = eng.reconstruct_assigned(list(asg), False, 7)
        if bad != -1:
            eng.set_assignment_lock([])
            print(f"   {tag:<10} NOT PACKABLE (block {bad} has no seat)",
                  flush=True)
            return None
        eng.build_occ(hz)
        eng.run_portfolio(ma._THR, T, 1, 0.05, 0.15, 0.002, 0.9995, 4242, 1,
                          [eng.get_solution()])
        o = eng.objective()
        eng.set_assignment_lock([])
        res = ma._check(prob, ma._recs_to_sol(ma._extract(eng, n),
                                              eng.entry_ranks()))
        return o[3], (res.get("feasible") if res else None), o

    a0 = run(cur, "A(current)")
    if a0 is None:
        print("   the shipped assignment is not packable from empty --"
              " the probe cannot compare", flush=True)
        return
    print(f"   A(current)  {a0[0]:,.0f}   checker={a0[1]}", flush=True)
    for k in (5, 15, 40, 100, None):
        asg, av = solve(k)
        if asg is None:
            print(f"   k={k}: CP failed", flush=True)
            continue
        moved = sum(1 for i in range(n) if asg[i] != cur[i])
        r = run(asg, f"k={k}")
        if r is None:
            continue
        d = (r[0] - a0[0]) / a0[0] * 100
        print(f"   k={str(k):<5} moved {moved:>3}  asg-cost {av:>12,.0f}"
              f"   annealed {r[0]:>14,.0f} ({d:+.2f}%)  checker={r[1]}"
              + ("   <-- BEATS CURRENT" if d < 0 and r[1] else ""), flush=True)


if __name__ == "__main__":
    main()
