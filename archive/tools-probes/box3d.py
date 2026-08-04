"""Direct 3D box-packing CP model: x, y, entry as CONTINUOUS decision
variables (no mined position vocabulary), pairwise 6-way separation.

Why this is sound: our engine's boards sit at 103% BBOX utilisation --
bounding boxes overlap by only 3% -- so a strict no-bbox-overlap model
gives up ~3% of reachable density while turning the problem into one
CP-SAT solves natively (disjunctive packing + scheduling propagators).
Bbox non-overlap in (x,y,t) also implies the crane descent/ascent rules
(a blocker must share (x,y) while present, which is exactly a 3D
overlap), so any solution is checker-legal by construction.

Usage: box3d.py <pnum> <seconds> [workers]
"""
import json, math, os, sys
from ortools.sat.python import cp_model

pn = int(sys.argv[1]); TL = float(sys.argv[2])
W = int(sys.argv[3]) if len(sys.argv) > 3 else 4
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
p = json.load(open(f"data/preliminary_test/prob_{pn}.json"))
B = p["blocks"]; bays = p["bays"]; n = len(B); m = len(bays)
w = p["weights"]
P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
R = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]
D = [int(round(b["due_date"])) for b in B]
WL = [b["workload"] for b in B]
PR = [b["bay_preferences"] for b in B]
H = max(D) + max(P) + 10

# integer bbox per (block, orientation), and the reference-point offset
dims = []
for b in B:
    ds = []
    for o, sh in enumerate(b["shape"]):
        vs = [v for L in sh["layers"] for v in L]
        xs = [v[0] for v in vs]; ys = [v[1] for v in vs]
        ds.append((int(math.ceil(max(xs) - min(xs))),
                   int(math.ceil(max(ys) - min(ys))),
                   min(xs), min(ys)))
    dims.append(ds)

mdl = cp_model.CpModel()
bay = [[mdl.NewBoolVar(f"b{i}_{j}") for j in range(m)] for i in range(n)]
ori = [[mdl.NewBoolVar(f"o{i}_{k}") for k in range(len(dims[i]))]
       for i in range(n)]
for i in range(n):
    mdl.AddExactlyOne(bay[i]); mdl.AddExactlyOne(ori[i])
maxW = max(x["width"] for x in bays); maxH = max(x["height"] for x in bays)
X = [mdl.NewIntVar(0, maxW, f"x{i}") for i in range(n)]
Y = [mdl.NewIntVar(0, maxH, f"y{i}") for i in range(n)]
E = [mdl.NewIntVar(R[i], H - P[i], f"e{i}") for i in range(n)]
TD = [mdl.NewIntVar(0, H, f"t{i}") for i in range(n)]
WD = [mdl.NewIntVar(0, maxW, f"w{i}") for i in range(n)]
HT = [mdl.NewIntVar(0, maxH, f"h{i}") for i in range(n)]
for i in range(n):
    mdl.AddMaxEquality(TD[i], [E[i] + P[i] - D[i], 0])
    for k, (wi, hi, _, _) in enumerate(dims[i]):
        mdl.Add(WD[i] == wi).OnlyEnforceIf(ori[i][k])
        mdl.Add(HT[i] == hi).OnlyEnforceIf(ori[i][k])
    for j in range(m):
        mdl.Add(X[i] + WD[i] <= bays[j]["width"]).OnlyEnforceIf(bay[i][j])
        mdl.Add(Y[i] + HT[i] <= bays[j]["height"]).OnlyEnforceIf(bay[i][j])

# pairwise: same bay => separated in x, y or time
for i in range(n):
    for j in range(i + 1, n):
        same = mdl.NewBoolVar("")
        for q in range(m):
            both = mdl.NewBoolVar("")
            mdl.AddBoolAnd([bay[i][q], bay[j][q]]).OnlyEnforceIf(both)
            mdl.AddBoolOr([bay[i][q].Not(), bay[j][q].Not()]).OnlyEnforceIf(both.Not())
            mdl.AddImplication(both, same)
        mdl.AddBoolOr([same] + [bay[i][q].Not() for q in range(m)]
                      + [bay[j][q].Not() for q in range(m)])
        lx = mdl.NewBoolVar(""); rx = mdl.NewBoolVar("")
        by = mdl.NewBoolVar(""); ay = mdl.NewBoolVar("")
        bt = mdl.NewBoolVar(""); at = mdl.NewBoolVar("")
        mdl.Add(X[i] + WD[i] <= X[j]).OnlyEnforceIf(lx)
        mdl.Add(X[j] + WD[j] <= X[i]).OnlyEnforceIf(rx)
        mdl.Add(Y[i] + HT[i] <= Y[j]).OnlyEnforceIf(by)
        mdl.Add(Y[j] + HT[j] <= Y[i]).OnlyEnforceIf(ay)
        mdl.Add(E[i] + P[i] <= E[j]).OnlyEnforceIf(bt)
        mdl.Add(E[j] + P[j] <= E[i]).OnlyEnforceIf(at)
        mdl.AddBoolOr([lx, rx, by, ay, bt, at, same.Not()])

areas = [x["width"] * x["height"] for x in bays]
uj = [sum(areas) / m / a for a in areas]
SC = 100
load = []
for j in range(m):
    lo = mdl.NewIntVar(0, int(sum(WL) * max(uj) * SC) + 1, "")
    mdl.Add(lo == sum(int(round(uj[j] * WL[i] * SC)) * bay[i][j]
                      for i in range(n)))
    load.append(lo)
z2 = mdl.NewIntVar(0, int(sum(WL) * max(uj) * SC) + 1, "z2")
for a in range(m):
    for b2 in range(m):
        if a != b2: mdl.Add(z2 >= load[a] - load[b2])
smax = [max(PR[i]) for i in range(n)]
mdl.Minimize(int(w["w1"]) * SC * sum(TD)
             + int(w["w2"]) * z2
             + SC * sum(int(w["w3"] * (smax[i] - PR[i][j])) * bay[i][j]
                        for i in range(n) for j in range(m)))

class CB(cp_model.CpSolverSolutionCallback):
    def __init__(self): super().__init__()
    def on_solution_callback(self):
        print(json.dumps({"t": round(self.WallTime(), 1),
                          "obj": self.ObjectiveValue() / SC}), flush=True)
sv = cp_model.CpSolver()
sv.parameters.max_time_in_seconds = TL
sv.parameters.num_search_workers = W
sv.parameters.log_search_progress = False
st = sv.Solve(mdl, CB())
print(json.dumps({"status": sv.StatusName(st),
                  "obj": sv.ObjectiveValue()/SC if st in
                  (cp_model.OPTIMAL, cp_model.FEASIBLE) else None,
                  "bound": sv.BestObjectiveBound()/SC}), flush=True)
if st in (cp_model.OPTIMAL, cp_model.FEASIBLE):
    out = {}
    for i in range(n):
        j = next(q for q in range(m) if sv.Value(bay[i][q]))
        k = next(q for q in range(len(dims[i])) if sv.Value(ori[i][q]))
        _, _, ox, oy = dims[i][k]
        e = sv.Value(E[i])
        out[i] = [j, sv.Value(X[i]) - int(math.floor(ox)),
                  sv.Value(Y[i]) - int(math.floor(oy)), k, e, e + P[i]]
    json.dump(out, open(f"runs/box3d_p{pn}.json", "w"))
    print("plan saved", flush=True)
