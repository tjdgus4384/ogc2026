"""Gurobi port of the certified pattern-recombination model.

Same certified inputs as mip_v3 (runs/pats_pN.json + compat_pN.json),
different solver dynamics: CP-SAT is hint-echo at 300k-pair all-free
scale (measured 08-01); Gurobi brings MIP presolve + RINS/heuristics.

Encoding (ties are certified structurally illegal, so a selected
overlapping pair is either time-disjoint or in an ALLOWED strict
quadrant): per constrained pair, mode binaries {D1, D2} + allowed
quadrants, big-M linked (H is small so M is tight), with
   li + lj - 1 <= sum(modes).
Usage: mip_grb.py <pnum> <seconds>
"""
import json
import math
import os
import sys

import gurobipy as gp
from gurobipy import GRB

pnum = int(sys.argv[1])
tl = float(sys.argv[2]) if len(sys.argv) > 2 else 1200.0
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

prob = json.load(open(f"data/preliminary_test/prob_{pnum}.json"))
n = len(prob["blocks"])
m = len(prob["bays"])
P = [max(1, int(math.ceil(b["processing_time"] - 1e-9)))
     for b in prob["blocks"]]
R = [int(math.ceil(b["release_time"] - 1e-9)) for b in prob["blocks"]]
D = [int(round(b["due_date"])) for b in prob["blocks"]]
WL = [b["workload"] for b in prob["blocks"]]
pref = [b["bay_preferences"] for b in prob["blocks"]]
w = prob["weights"]

pats = [[tuple(k) for k in row]
        for row in json.load(open(f"runs/pats_p{pnum}.json"))]
compat = json.load(open(f"runs/compat_p{pnum}.json"))

hint_path = os.environ.get(
    "HINT", f"runs/mipboards/prob_{pnum}.sol.json")
best = json.load(open(hint_path))
hent, hmeta = {}, {}
for t, ops in best["operations"].items():
    for op in ops:
        if op["type"] == "ENTRY":
            hent[op["block_id"]] = int(t)
            hmeta[op["block_id"]] = (op["bay_id"], op["orient_idx"],
                                     op["x"], op["y"])
H = max(D) + max(P) + 5
for i, e0 in hent.items():
    H = max(H, e0 + P[i] + 1)

mdl = gp.Model("recomb")
mdl.Params.TimeLimit = tl
mdl.Params.Threads = int(os.environ.get("GRBTHREADS", "16"))
mdl.Params.MIPFocus = int(os.environ.get("MIPFOCUS", "1"))
mdl.Params.Heuristics = float(os.environ.get("HEUR", "0.3"))

lit = [[mdl.addVar(vtype=GRB.BINARY, name=f"p{i}_{k}")
        for k in range(len(pats[i]))] for i in range(n)]
e = [mdl.addVar(vtype=GRB.INTEGER, lb=R[i], ub=H - P[i], name=f"e{i}")
     for i in range(n)]
td = [mdl.addVar(lb=0.0, name=f"t{i}") for i in range(n)]
for i in range(n):
    mdl.addConstr(gp.quicksum(lit[i]) == 1)
    mdl.addConstr(td[i] >= e[i] + P[i] - D[i])

BIGM = H + max(P) + 2
nc = 0
QUADS = [(-1, -1), (-1, 1), (1, -1), (1, 1)]
for key, v in compat.items():
    i, ki, j, kj = map(int, key.split(","))
    if all(x is True for x in v):
        continue
    li, lj = lit[i][ki], lit[j][kj]
    # hint-active mode for a COMPLETE warm start (both blocks hinted
    # to these patterns -> exactly one mode must start at 1)
    hact = None
    if i in hent and j in hent and hmeta.get(i) == pats[i][ki] \
            and hmeta.get(j) == pats[j][kj]:
        hei, hej = hent[i], hent[j]
        hfi, hfj = hei + P[i], hej + P[j]
        if hfi <= hej:
            hact = "d1"
        elif hfj <= hei:
            hact = "d2"
        else:
            hact = ((hei > hej) - (hei < hej),
                    (hfi > hfj) - (hfi < hfj))
    modes = []
    d1 = mdl.addVar(vtype=GRB.BINARY)
    mdl.addConstr(e[j] - e[i] >= P[i] - BIGM * (1 - d1))
    d1.Start = 1.0 if hact == "d1" else 0.0
    modes.append(d1)
    d2 = mdl.addVar(vtype=GRB.BINARY)
    mdl.addConstr(e[i] - e[j] >= P[j] - BIGM * (1 - d2))
    d2.Start = 1.0 if hact == "d2" else 0.0
    modes.append(d2)
    for (re_, rf) in QUADS:
        if v[(re_ + 1) * 3 + (rf + 1)] is not True:
            continue
        q = mdl.addVar(vtype=GRB.BINARY)
        # sign(e_i - e_j) == re_ (strict)
        if re_ < 0:
            mdl.addConstr(e[j] - e[i] >= 1 - BIGM * (1 - q))
        else:
            mdl.addConstr(e[i] - e[j] >= 1 - BIGM * (1 - q))
        # sign(f_i - f_j) == rf (strict), f = e + P
        dp = P[i] - P[j]
        if rf < 0:
            mdl.addConstr(e[j] - e[i] >= 1 + dp - BIGM * (1 - q))
        else:
            mdl.addConstr(e[i] - e[j] >= 1 - dp - BIGM * (1 - q))
        q.Start = 1.0 if hact == (re_, rf) else 0.0
        modes.append(q)
    mdl.addConstr(gp.quicksum(modes) >= li + lj - 1)
    nc += 1
print(json.dumps({"constrained_pairs": nc}), flush=True)

areas = [by["width"] * by["height"] for by in prob["bays"]]
uj = [sum(areas) / m / a for a in areas]
load = []
for jb in range(m):
    lo = gp.quicksum(uj[jb] * WL[i] * lit[i][k]
                     for i in range(n) for k in range(len(pats[i]))
                     if pats[i][k][0] == jb)
    load.append(lo)
z2 = mdl.addVar(lb=0.0, name="z2")
for a in range(m):
    for b in range(m):
        if a != b:
            mdl.addConstr(z2 >= load[a] - load[b])
smax = [max(pref[i]) for i in range(n)]
mdl.setObjective(
    w["w1"] * gp.quicksum(td) + w["w2"] * z2 +
    w["w3"] * gp.quicksum((smax[i] - pref[i][pats[i][k][0]]) * lit[i][k]
                          for i in range(n)
                          for k in range(len(pats[i]))),
    GRB.MINIMIZE)

nh = 0
for i in range(n):
    if i in hmeta and hmeta[i] in pats[i]:
        for k in range(len(pats[i])):
            lit[i][k].Start = 1.0 if pats[i][k] == hmeta[i] else 0.0
        e[i].Start = hent[i]
        nh += 1
print(json.dumps({"warm_started": nh}), flush=True)


def cb(model, where):
    if where == GRB.Callback.MIPSOL:
        print(json.dumps(
            {"t": round(model.cbGet(GRB.Callback.RUNTIME), 1),
             "obj": model.cbGet(GRB.Callback.MIPSOL_OBJ)}), flush=True)


mdl.optimize(cb)
print(json.dumps({"status": int(mdl.Status),
                  "obj": mdl.ObjVal if mdl.SolCount else None,
                  "bound": mdl.ObjBound}), flush=True)
if not mdl.SolCount:
    sys.exit(0)

out = {}
for i in range(n):
    k = max(range(len(pats[i])), key=lambda kk: lit[i][kk].X)
    b_, o_, x_, y_ = pats[i][k]
    ei = int(round(e[i].X))
    out[i] = [b_, x_, y_, o_, ei, ei + P[i]]
json.dump(out, open(f"runs/mip_grb_p{pnum}.json", "w"))
print("plan saved", flush=True)
