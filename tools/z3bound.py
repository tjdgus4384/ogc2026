"""Z3 lower bound at Z1=0: assignment ILP under per-bay area-day budgets.

min sum_i (Smax_i - S_i,a(i))  s.t.  for each bay j:
    sum_{i: a(i)=j} groundArea_i * P_i <= cap_j * span   (aggregate area-days)
    a(i) only among bays whose dims fit some orientation.
The aggregate budget is NECESSARY for any Z1=0 solution (every on-time stay
consumes its area-days within the span), so the optimum bounds Z3 from
below for Z1=0 solutions. Also reports the bound with tightened budgets
(util factor u) to show sensitivity to packing efficiency.

Usage: python z3bound.py <prob.json>
"""
import json
import math
import sys

from ortools.sat.python import cp_model


def poly_area(pts):
    s = 0.0
    for k in range(len(pts)):
        x1, y1 = pts[k]; x2, y2 = pts[(k + 1) % len(pts)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2


def bound(prob, util):
    B, bays = prob["blocks"], prob["bays"]
    n, m = len(B), len(bays)
    R = [b["release_time"] for b in B]
    D = [b["due_date"] for b in B]
    P = [b["processing_time"] for b in B]
    span = max(D) - min(R)
    cap = [by["width"] * by["height"] * span * util for by in bays]
    ground = [poly_area(b["shape"][0]["layers"][0]) for b in B]
    pref = [b["bay_preferences"] for b in B]

    def fits(b, by):
        for o in b["shape"]:
            vs = [v for L in o["layers"] for v in L]
            w = max(v[0] for v in vs) - min(v[0] for v in vs)
            h = max(v[1] for v in vs) - min(v[1] for v in vs)
            if w <= by["width"] + 1e-9 and h <= by["height"] + 1e-9:
                return True
        return False

    md = cp_model.CpModel()
    SC = 1000
    x = {}
    for i, b in enumerate(B):
        opts = [(j, md.NewBoolVar(f"x{i}_{j}")) for j, by in enumerate(bays)
                if fits(b, by)]
        md.AddExactlyOne(v for _, v in opts)
        for j, v in opts:
            x[i, j] = v
    for j in range(m):
        md.Add(sum(int(ground[i] * P[i] * SC) * x[i, j]
                   for i in range(len(B)) if (i, j) in x)
               <= int(cap[j] * SC))
    md.Minimize(sum(int((max(pref[i]) - pref[i][j]) * SC) * x[i, j]
                    for (i, j) in x))
    sv = cp_model.CpSolver()
    sv.parameters.max_time_in_seconds = 60
    st = sv.Solve(md)
    return (sv.StatusName(st), sv.ObjectiveValue() / SC
            if st in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None)


def main():
    prob = json.load(open(sys.argv[1]))
    for u in (1.0, 0.9, 0.8, 0.7):
        st, z3 = bound(prob, u)
        print(json.dumps({"util": u, "status": st, "z3_lb": z3}))


if __name__ == "__main__":
    main()
