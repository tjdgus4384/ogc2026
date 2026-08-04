"""Gurobi variant of the pattern-recombination model (exact checker
objective). Same mining as mip_recomb; disjunctive via indicator
constraints. Usage: grb_recomb.py <pnum> <TL_s>"""
import glob
import json
import math
import sys

import gurobipy as gp
from gurobipy import GRB

pnum = int(sys.argv[1])
TL = int(sys.argv[2]) if len(sys.argv) > 2 else 1200
prob = json.load(open(f"data/preliminary_test/prob_{pnum}.json"))
n = len(prob["blocks"]); m = len(prob["bays"]); w = prob["weights"]
D = [int(round(b["due_date"])) for b in prob["blocks"]]
R = [int(math.ceil(b["release_time"] - 1e-9)) for b in prob["blocks"]]
P = [max(1, int(math.ceil(b["processing_time"] - 1e-9)))
     for b in prob["blocks"]]
W = [b["workload"] for b in prob["blocks"]]
pref = [b["bay_preferences"] for b in prob["blocks"]]
H = max(D) + max(P) + 40

def bbox(blk, oi):
    vs = [v for L in prob["blocks"][blk]["shape"][oi]["layers"] for v in L]
    xs = [v[0] for v in vs]; ys = [v[1] for v in vs]
    return min(xs), min(ys), max(xs), max(ys)

menus = [dict() for _ in range(n)]
for f in glob.glob(f"runs/*/prob_{pnum}.sol.json"):
    try:
        sol = json.load(open(f))
    except Exception:
        continue
    for t, ops in sol.get("operations", {}).items():
        for op in ops:
            if op["type"] == "ENTRY":
                k = (op["bay_id"], op["orient_idx"], op["x"], op["y"])
                menus[op["block_id"]][k] = \
                    menus[op["block_id"]].get(k, 0) + 1
pats = [[k for k, _ in sorted(mn.items(), key=lambda t: -t[1])[:10]]
        for mn in menus]
pbb = [[(b_,) + tuple(v + o for v, o in zip(bbox(i, o_)[:2], (x_, y_)))
        + tuple(v + o for v, o in zip(bbox(i, o_)[2:], (x_, y_)))
        for (b_, o_, x_, y_) in pats[i]] for i in range(n)]

mdl = gp.Model()
mdl.Params.TimeLimit = TL
mdl.Params.Threads = 16
lit = [mdl.addVars(len(pats[i]), vtype=GRB.BINARY) for i in range(n)]
for i in range(n):
    mdl.addConstr(lit[i].sum() == 1)
e = mdl.addVars(n, lb=[R[i] for i in range(n)],
                ub=[H - P[i] for i in range(n)], vtype=GRB.INTEGER)
td = mdl.addVars(n, lb=0.0)
for i in range(n):
    mdl.addConstr(td[i] >= e[i] + P[i] - D[i])

def ov(a, b):
    return a[1] < b[3] and b[1] < a[3] and a[2] < b[4] and b[2] < a[4]

nd = 0
for i in range(n):
    for j in range(i + 1, n):
        pairs = [(ki, kj) for ki, bi in enumerate(pbb[i])
                 for kj, bj in enumerate(pbb[j])
                 if bi[0] == bj[0] and ov(bi, bj)]
        if not pairs:
            continue
        # one ordering var per (i, j); conflict activation per pattern pair
        o = mdl.addVar(vtype=GRB.BINARY)
        for ki, kj in pairs:
            c = mdl.addVar(vtype=GRB.BINARY)
            mdl.addConstr(c >= lit[i][ki] + lit[j][kj] - 1)
            mdl.addGenConstrIndicator(c, True, e[i] + P[i] - e[j]
                                      <= H * (1 - 0) * 0 + H * o)
            mdl.addGenConstrIndicator(c, True, e[j] + P[j] - e[i]
                                      <= H * (1 - o))
            nd += 1
print(json.dumps({"disjunctions": nd}), flush=True)

areas = [by["width"] * by["height"] for by in prob["bays"]]
uj = [sum(areas) / m / a for a in areas]
load = mdl.addVars(m, lb=0.0)
for j in range(m):
    mdl.addConstr(load[j] == gp.quicksum(
        uj[j] * W[i] * lit[i][k] for i in range(n)
        for k in range(len(pats[i])) if pats[i][k][0] == j))
z2 = mdl.addVar(lb=0.0)
for a in range(m):
    for b in range(m):
        if a != b:
            mdl.addConstr(z2 >= load[a] - load[b])
smax = [max(pref[i]) for i in range(n)]
mdl.setObjective(
    w["w1"] * gp.quicksum(td[i] for i in range(n)) + w["w2"] * z2 +
    w["w3"] * gp.quicksum((smax[i] - pref[i][pats[i][k][0]]) * lit[i][k]
                          for i in range(n)
                          for k in range(len(pats[i]))), GRB.MINIMIZE)
mdl.optimize()
if mdl.SolCount:
    out = {}
    for i in range(n):
        k = next(k for k in range(len(pats[i]))
                 if lit[i][k].X > 0.5)
        b_, o_, x_, y_ = pats[i][k]
        ei = int(round(e[i].X))
        out[i] = [b_, x_, y_, o_, ei, ei + P[i]]
    json.dump(out, open(f"runs/grb_recomb_p{pnum}.json", "w"))
    print(json.dumps({"obj": mdl.ObjVal, "bound": mdl.ObjBound,
                      "gap": mdl.MIPGap}), flush=True)
