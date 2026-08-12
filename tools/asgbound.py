#!/usr/bin/env python3
"""The geometry-free assignment optimum: a real lower bound, and P3's whole story.

Two facts make this exact rather than approximate:

    Z2 = floor(max_a u_a*L_a - min_a u_a*L_a)     L_a = workload assigned to bay a
    Z3 = sum_i (Smax_i - pref_i[bay_i])

Both depend on the BAY ASSIGNMENT ALONE -- geometry enters the problem only as
feasibility. So dropping geometry entirely leaves a well-posed problem over the
true objective, and its optimum is a valid lower bound on the true optimum.

On P3 that is not just a bound, it is the whole objective: Z1 = 0 on every draw
we have ever recorded (48/48 stable), so P3 = w2*Z2 + w3*Z3 exactly, and the
geometry-free optimum is the best any solver could do if packing were free.

    bound far below the all-team minimum   assignment slack exists and the
                                           question becomes which assignments
                                           are packable;
    bound near the all-team minimum        the assignment is nearly forced, and
                                           our 82,610 against 64,695 is a
                                           search failure, not a packing one.

A day of probes asked "can this board improve"; not one asked where the floor
is. This asks. It also traces the Z2/Z3 frontier, because the two terms trade
against each other at a fixed exchange rate (on P3, 150 per preference point
against 5 per unit of imbalance = 30:1) and the trade is the whole design.

    python3 tools/asgbound.py prob_3 [seconds]
"""
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
SC = 1000              # fixed-point scale for the fractional bay weights u_j


def main():
    prob_id = sys.argv[1]
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 120.0
    from ortools.sat.python import cp_model as cp
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    B, bays, W = prob["blocks"], prob["bays"], prob["weights"]
    n, m = len(B), len(bays)
    areas = [b["width"] * b["height"] for b in bays]
    u = [sum(areas) / m / a for a in areas]
    wl = [b["workload"] for b in B]
    PREF = [[float(v) for v in b["bay_preferences"]] for b in B]
    SMAX = [max(p) for p in PREF]
    # regret is integral on these instances; assert rather than silently round
    REG = [[int(round(SMAX[i] - PREF[i][j])) for j in range(m)] for i in range(n)]
    uw = [int(round(SC * u[j])) for j in range(m)]
    print(f"{prob_id}: n={n} m={m}  w2={W['w2']} w3={W['w3']}"
          f"   u={[round(x,3) for x in u]}")

    def solve(z2cap=None):
        mdl = cp.CpModel()
        x = [[mdl.NewBoolVar(f"x{i}_{j}") for j in range(m)] for i in range(n)]
        for i in range(n):
            mdl.AddExactlyOne(x[i])
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
        spread = mdl.NewIntVar(0, max(uw) * tot, "spread")   # = SC * Z2
        mdl.Add(spread == hi - lo)
        z3 = mdl.NewIntVar(0, sum(max(r) for r in REG), "z3")
        mdl.Add(z3 == sum(REG[i][j] * x[i][j] for i in range(n) for j in range(m)))
        if z2cap is not None:
            mdl.Add(spread <= int(z2cap * SC))
        mdl.Minimize(W["w2"] * spread + W["w3"] * SC * z3)
        sv = cp.CpSolver()
        sv.parameters.max_time_in_seconds = secs
        sv.parameters.num_search_workers = 8
        st = sv.Solve(mdl)
        if st not in (cp.OPTIMAL, cp.FEASIBLE):
            return None
        return (sv.StatusName(st), sv.Value(spread) / SC, sv.Value(z3),
                sv.ObjectiveValue() / SC, sv.BestObjectiveBound() / SC)

    r = solve()
    if r is None:
        print("   no solution")
        return
    name, z2, z3, obj, bnd = r
    print(f"   geometry-free optimum: {obj:,.0f}   ({name}, proven bound {bnd:,.0f})")
    print(f"      Z2={z2:,.0f} (w2*Z2={W['w2']*z2:,.0f})   "
          f"Z3={z3:,.0f} (w3*Z3={W['w3']*z3:,.0f})")
    print(f"   frontier (min Z3 at a capped Z2):")
    for cap in (0, 200, 500, 1000, 2000, 3262):
        rr = solve(cap)
        if rr:
            _, a, b, c, _ = rr
            print(f"      Z2 <= {cap:>5}   ->  Z2={a:>7,.0f}  Z3={b:>5,.0f}"
                  f"   objective {c:>10,.0f}")


if __name__ == "__main__":
    main()
