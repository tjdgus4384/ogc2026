#!/usr/bin/env python3
"""Realize the master's (bay, entry) schedule through the real kernel.

master.py answers whether a better schedule EXISTS at a given density; this
answers whether the kernel can BUILD it. The two failures the ledger records
for schedule-like locks do not apply here, and the distinction is the point:

    asgcap pinned assignments into a FINISHED board       (bay full of the
                                                           blocks that filled it)
    the 40% argmax lock chose bays with no capacity model (collapse measured)

Here the board starts EMPTY, the schedule respects per-(bay, day) capacity by
construction, entries are scheduled -- not left to greedy -- and insertion
follows the master's own day order, which is exactly the order the greedy
itself builds in.

Procedure: solve the master at rho, then insert blocks in ascending scheduled
day (long stays first within a day), each forced to its (bay, entry <= d).
Whatever fails its lock gets a second pass in its bay unconstrained, then a
third anywhere -- those are the schedule's real casualties. Score on the
engine, verify with the official checker, and print master-predicted vs
realized. The realization gap IS the measurement:

    realized ~ predicted    the schedule survives geometry; wire it in as the
                            construction stage and gate it;
    realized >> predicted   cumulative capacity is too loose a model of
                            packability at this rho -- lower rho or add cuts.

    python3 tools/realize.py prob_4 0.65 [secs]
"""
import json
import math
import os
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


def solve_master(prob, rho, secs):
    from ortools.sat.python import cp_model as cp
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
    AR = [int(round(min(max(poly_area(l) for l in sh["layers"])
                        for sh in b["shape"]))) for b in B]
    H = max(D) + 64
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
        iv = [mdl.NewOptionalIntervalVar(s[i], P[i], e[i], x[i][j], f"iv{i}_{j}")
              for i in range(n)]
        mdl.AddCumulative(iv, AR, int(rho * A[j]))
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
    sv.parameters.max_time_in_seconds = secs
    sv.parameters.num_search_workers = 8
    st = sv.Solve(mdl)
    if st not in (2, 4):          # FEASIBLE, OPTIMAL
        return None
    sched = [(next(j for j in range(m) if sv.Value(x[i][j])), sv.Value(s[i]))
             for i in range(n)]
    return {"status": sv.StatusName(st), "obj": sv.ObjectiveValue() / SC,
            "z1": sv.Value(z1), "z2": sv.Value(spread) / SC,
            "z3": sv.Value(z3), "sched": sched}


def main():
    prob_id = sys.argv[1]
    rho = float(sys.argv[2])
    secs = float(sys.argv[3]) if len(sys.argv) > 3 else 240.0
    sys.path.insert(0, str(REPO / "solver"))
    os.environ.setdefault("OGC_SEED", "7")
    import myalgorithm as ma
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    n = len(prob["blocks"])

    t0 = time.time()
    M = solve_master(prob, rho, secs)
    if M is None:
        print(f"{prob_id} rho {rho}: master infeasible/unsolved")
        return
    print(f"{prob_id} rho {rho}: master {M['status']} obj {M['obj']:,.0f} "
          f"(Z1={M['z1']} Z2={M['z2']:,.0f} Z3={M['z3']})  "
          f"target {TGT[prob_id]:,}  our median {OURS[prob_id]:,}  "
          f"[{time.time()-t0:.0f}s]")

    eng, D, P = ma._fresh(prob, 4)
    st = [0] * (n * 7)
    eng.set_solution(st)
    eng.build_occ(eng.horizon())
    order = sorted(range(n), key=lambda i: (M["sched"][i][1], -P[i]))
    exact = inbay = free = lost = 0
    for i in order:
        j, d = M["sched"][i]
        if eng.insert_capped(i, d, j):
            exact += 1
        elif eng.insert_capped(i, -1, j):
            inbay += 1
        elif eng.insert_capped(i, -1, -1):
            free += 1
        else:
            lost += 1
    o = eng.objective()
    sol = ma._recs_to_sol(ma._extract(eng, n), eng.entry_ranks())
    res = ma._check(prob, sol)
    feas = res.get("feasible") if res else None
    print(f"   realized: {o[3]:>12,.0f}  Z1={o[0]:.0f} Z2={o[1]:.0f} Z3={o[2]:.0f}"
          f"   checker feasible={feas}")
    print(f"   locks: {exact} at schedule, {inbay} late-in-bay, "
          f"{free} anywhere, {lost} unplaced"
          f"   realization gap {(o[3]-M['obj'])/M['obj']*100:+.1f}%"
          f"   vs our median {(o[3]-OURS[prob_id])/OURS[prob_id]*100:+.1f}%")


if __name__ == "__main__":
    main()
