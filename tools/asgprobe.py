#!/usr/bin/env python3
"""How much of the assignment optimum is actually packable?

asgbound proved the geometry-free optimum exactly. On P3 it is 36,759 at
Z2=6,632 / Z3=24, and the frontier says our own board is not merely short of it
but short in a specific way: at OUR balance level (Z2 3,256) the best possible
regret is Z3=253, and we carry 414. The all-team minimum needs only Z3 <= 323.
So P3's remaining 21.7% is preference regret, in a yard that peaks at 41.7%
full -- not a packing problem at all.

And the mechanism is already in the driver, already active on P3:

    for i in spread[:int(nn * 0.4)]:                        # top 40% by spread
        asg[i] = max(range(len(pref[i])), key=lambda j: pref[i][j])

That picks each block's argmax bay INDEPENDENTLY. It cannot see Z2, which is a
function of the whole assignment, and it cannot see that two blocks wanting the
same bay may be better served by splitting them. The gap asgbound measures is
exactly the price of that myopia.

Before changing the rule, find out what geometry actually permits. Lock the
CP-optimal assignment FULLY and run the kernel against it. A full lock is the
harshest possible test -- the driver's own comment says full locks are refuted
because tardiness explodes -- so this is not a candidate, it is a measurement of
where packability stops:

    Z1 stays 0        the assignment optimum is reachable and P3 is solved by
                      assignment alone;
    Z1 explodes       geometry binds, and the useful lock is partial -- then the
                      question is how much of the optimum survives.

    python3 tools/asgprobe.py SOLVERDIR prob_3 SECONDS [seed]
"""
import json
import os
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
SC = 1000


def solve_asg(prob, secs, z2cap=None):
    from ortools.sat.python import cp_model as cp
    B, bays, W = prob["blocks"], prob["bays"], prob["weights"]
    n, m = len(B), len(bays)
    areas = [b["width"] * b["height"] for b in bays]
    u = [sum(areas) / m / a for a in areas]
    wl = [b["workload"] for b in B]
    PREF = [[float(v) for v in b["bay_preferences"]] for b in B]
    REG = [[int(round(max(PREF[i]) - PREF[i][j])) for j in range(m)]
           for i in range(n)]
    uw = [int(round(SC * u[j])) for j in range(m)]
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
    spread = mdl.NewIntVar(0, max(uw) * tot, "sp")
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
        return None, None
    A = [next(j for j in range(m) if sv.Value(x[i][j])) for i in range(n)]
    return A, sv.ObjectiveValue() / SC


def main():
    solver_dir, prob_id = sys.argv[1], sys.argv[2]
    secs = float(sys.argv[3])
    seed = int(sys.argv[4]) if len(sys.argv) > 4 else 7
    sys.path.insert(0, str(REPO / solver_dir))
    os.environ["OGC_SEED"] = str(seed)
    import myalgorithm as ma
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    n = len(prob["blocks"])
    print(f"{prob_id}: n={n}, anneal {secs:.0f}s per arm, seed {seed}")

    def run(A, label):
        t0 = time.time()
        eng, seeds = ma._seed_free(prob, asg=A)
        if eng is None:
            print(f"   {label:<28} SEED FAILED (nothing places under this lock)")
            return
        s0 = eng.objective()
        if A is not None:
            eng.set_assignment_lock(A)
        starts = [seeds[0], []] + seeds[1:3] if seeds else []
        eng.run_portfolio(4, secs, 1, 0.05, 0.15, 0.002, 0.9995, seed, 1, starts)
        if A is not None:
            eng.set_assignment_lock([])
        o = eng.objective()
        print(f"   {label:<28} {o[3]:>12,.0f}   Z1={o[0]:>6.0f} Z2={o[1]:>6.0f}"
              f" Z3={o[2]:>5.0f}   (seed board {s0[3]:,.0f}, {time.time()-t0:.0f}s)")

    run(None, "shipped (no lock)")
    for cap in (None, 3262, 1000):
        t0 = time.time()
        A, obj = solve_asg(prob, 60.0, cap)
        if A is None:
            print(f"   CP failed at cap={cap}")
            continue
        tag = "unconstrained" if cap is None else f"Z2<={cap}"
        print(f"   -- CP assignment {tag}: geometry-free {obj:,.0f}"
              f"  ({time.time()-t0:.0f}s)")
        run(A, f"full lock, {tag}")


if __name__ == "__main__":
    main()
