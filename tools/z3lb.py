"""Lower bound on regret (Z3) under the area relaxation.

Blocks keep their release/due windows and must be punctual, but their
SHAPE is relaxed to pure area against a per-bay daily capacity. Any
real board satisfies this, so the optimum here is a valid floor on the
regret any punctual solution can achieve -- it tells us whether our
assignment is near-optimal or whether real room remains.
Usage: z3lb.py <pnum> <seconds>
"""
import json, math, sys
from ortools.sat.python import cp_model

pn = int(sys.argv[1]); TL = float(sys.argv[2]) if len(sys.argv) > 2 else 300
p = json.load(open(f"data/preliminary_test/prob_{pn}.json"))
B, bays = p["blocks"], p["bays"]
n, m = len(B), len(bays)

def shoe(pts):
    s = 0.0
    for k in range(len(pts)):
        x1, y1 = pts[k]; x2, y2 = pts[(k+1) % len(pts)]
        s += x1*y2 - x2*y1
    return abs(s)/2

area = [int(round(min(max(shoe(L) for L in sh["layers"])
                      for sh in b["shape"]))) for b in B]
P = [max(1, int(math.ceil(b["processing_time"]-1e-9))) for b in B]
R = [int(math.ceil(b["release_time"]-1e-9)) for b in B]
D = [int(round(b["due_date"])) for b in B]
pref = [b["bay_preferences"] for b in B]
smax = [max(pr) for pr in pref]

mdl = cp_model.CpModel()
x = [[mdl.NewBoolVar("") for j in range(m)] for i in range(n)]
st = [mdl.NewIntVar(R[i], max(R[i], D[i]-P[i]), "") for i in range(n)]
for i in range(n):
    mdl.AddExactlyOne(x[i])
for j in range(m):
    ivs, dems = [], []
    for i in range(n):
        ivs.append(mdl.NewOptionalIntervalVar(st[i], P[i], st[i]+P[i],
                                              x[i][j], ""))
        dems.append(area[i])
    mdl.AddCumulative(ivs, dems, int(bays[j]["width"]*bays[j]["height"]))
mdl.Minimize(sum((smax[i]-pref[i][j])*x[i][j]
                 for i in range(n) for j in range(m)))
sv = cp_model.CpSolver()
sv.parameters.max_time_in_seconds = TL
sv.parameters.num_search_workers = 8
r = sv.Solve(mdl)
ok = r in (cp_model.OPTIMAL, cp_model.FEASIBLE)
print(json.dumps({"P": pn, "status": sv.StatusName(r),
                  "Z3_lower_bound": sv.BestObjectiveBound(),
                  "Z3_relaxed_solution": sv.ObjectiveValue() if ok else None}))
