"""Lower bound on total tardiness (Z1) under the area relaxation.

Each block keeps its release date and processing time and must occupy
its own area for that long in SOME bay; shapes are relaxed to pure
area against each bay's daily capacity. Every real board satisfies
this, so the optimum is a valid floor: it separates the tardiness that
the instance forces from the tardiness our packing adds.
Usage: z1lb.py <prob.json path | prelim pnum> <seconds>
"""
import json, math, sys
from ortools.sat.python import cp_model

arg = sys.argv[1]; TL = float(sys.argv[2]) if len(sys.argv) > 2 else 300
path = arg if arg.endswith(".json") else f"data/preliminary_test/prob_{arg}.json"
p = json.load(open(path))
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
H = max(D) + max(P) + 30

mdl = cp_model.CpModel()
x = [[mdl.NewBoolVar("") for j in range(m)] for i in range(n)]
st = [mdl.NewIntVar(R[i], H - P[i], "") for i in range(n)]
td = [mdl.NewIntVar(0, H, "") for i in range(n)]
for i in range(n):
    mdl.AddExactlyOne(x[i])
    mdl.AddMaxEquality(td[i], [st[i] + P[i] - D[i], 0])
for j in range(m):
    ivs, dems = [], []
    for i in range(n):
        ivs.append(mdl.NewOptionalIntervalVar(st[i], P[i], st[i]+P[i],
                                              x[i][j], ""))
        dems.append(area[i])
    mdl.AddCumulative(ivs, dems, int(bays[j]["width"]*bays[j]["height"]))
mdl.Minimize(sum(td))
sv = cp_model.CpSolver()
sv.parameters.max_time_in_seconds = TL
sv.parameters.num_search_workers = 8
r = sv.Solve(mdl)
ok = r in (cp_model.OPTIMAL, cp_model.FEASIBLE)
print(json.dumps({"board": path.split("/")[-1][:-5],
                  "set": path.split("/")[-2], "status": sv.StatusName(r),
                  "Z1_lower_bound": sv.BestObjectiveBound(),
                  "Z1_relaxed": sv.ObjectiveValue() if ok else None}))
