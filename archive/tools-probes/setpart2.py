"""Set-partitioning with item ownership -- the strong recombination master.

Exactness comes from one fact: a SUBSET of a feasible bay plan is still
feasible (dropping blocks removes constraints, never adds any). So the
master may pick overlapping columns and then decide, per block, WHICH
chosen column keeps it; every other chosen column simply drops it. The
resulting board is feasible by construction and can reproduce any input
board exactly, so the optimum is never worse than the best input.

    x_c            column c (a certified bay plan) is chosen
    y_{i,c}        block i is kept by column c        (i in S_c, y <= x)
    sum_c y_{i,c} = 1                                  every block once
    sum_{c in bay j} x_c = 1                           one plan per bay
    zmax >= sum u_j L_i y_{i,c} over bay j  (and zmin <= ...)   -> Z2
    min  sum_{i,c} (w1*t_{i,c} + w3*r_{i,c}) y_{i,c} + w2*(zmax - zmin)

t_{i,c} is i's tardiness at the position/entry column c gives it, so
the cost is the TRUE objective of the recombined board -- no proxy.
This is what no local move class can do: it mixes whole bay geometries
from different runs and re-owns individual blocks across them.

Usage: setpart2.py <prob.json> <out.json> <pool.json|sol.json> [...]
Env:   SP_TL (MIP seconds, default 180)
"""
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "solver"))
os.chdir(ROOT)
import myalgorithm as M  # noqa: E402
import gurobipy as gp  # noqa: E402
from gurobipy import GRB  # noqa: E402

prob_path, out_path = sys.argv[1], sys.argv[2]
sources = sys.argv[3:]

p = json.load(open(prob_path))
B, bays, W = p["blocks"], p["bays"], p["weights"]
n, m = len(B), len(bays)
D = [int(round(b["due_date"])) for b in B]
P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
PREF = [[float(v) for v in b["bay_preferences"]] for b in B]
SMAX = [max(pr) for pr in PREF]
L = [float(b["workload"]) for b in B]
ar = [b["width"] * b["height"] for b in bays]
u = [(sum(ar) / m) / a for a in ar]
w1, w2, w3 = float(W["w1"]), float(W["w2"]), float(W["w3"])

cols = []          # dict(bay, plan {i:(ori,x,y,e)})
inputs = []        # full boards, for the "best input" reference


def add_board(flat):
    inputs.append(list(flat))
    for j in range(m):
        plan = {i: (flat[i*7+1], flat[i*7+2], flat[i*7+3], flat[i*7+4])
                for i in range(n) if flat[i*7+6] and flat[i*7] == j}
        if plan:
            cols.append({"bay": j, "plan": plan})


for src in sources:
    d = json.load(open(src))
    if "operations" in d:
        flat = [0] * (n * 7)
        for t, day in d["operations"].items():
            for o in day:
                i = o["block_id"]
                if o["type"] == "ENTRY":
                    flat[i*7:i*7+5] = [o["bay_id"], o["orient_idx"], o["x"],
                                       o["y"], int(t)]
                    flat[i*7+6] = 1
                else:
                    flat[i*7+5] = int(t)
        add_board(flat)
    else:
        for c in d["columns"]:
            cols.append({"bay": c["bay"],
                         "plan": {int(k): tuple(v) for k, v in c["plan"].items()}})

# dedupe
seen, uniq = set(), []
for c in cols:
    key = (c["bay"], tuple(sorted((i, *v) for i, v in c["plan"].items())))
    if key in seen:
        continue
    seen.add(key)
    uniq.append(c)
cols = uniq

mdl = gp.Model(env=gp.Env(params={"OutputFlag": 0}))
x = [mdl.addVar(vtype=GRB.BINARY) for _ in cols]
y = {}
for c, col in enumerate(cols):
    for i in col["plan"]:
        y[i, c] = mdl.addVar(vtype=GRB.BINARY)
        mdl.addConstr(y[i, c] <= x[c])
for j in range(m):
    idx = [c for c in range(len(cols)) if cols[c]["bay"] == j]
    mdl.addConstr(gp.quicksum(x[c] for c in idx) == 1)
for i in range(n):
    idx = [c for c in range(len(cols)) if i in cols[c]["plan"]]
    if not idx:
        sys.exit(json.dumps({"error": f"block {i} in no column"}))
    mdl.addConstr(gp.quicksum(y[i, c] for c in idx) == 1)
zmax = mdl.addVar(lb=0.0); zmin = mdl.addVar(lb=0.0)
for j in range(m):
    ld = gp.quicksum(u[j] * L[i] * y[i, c] for c in range(len(cols))
                     if cols[c]["bay"] == j for i in cols[c]["plan"])
    mdl.addConstr(zmax >= ld); mdl.addConstr(zmin <= ld)
obj = gp.LinExpr()
for c, col in enumerate(cols):
    j = col["bay"]
    for i, (oi, xx, yy, e) in col["plan"].items():
        obj += (w1 * max(0, e + P[i] - D[i]) + w3 * (SMAX[i] - PREF[i][j])) * y[i, c]
obj += w2 * (zmax - zmin)
mdl.setObjective(obj, GRB.MINIMIZE)
mdl.Params.TimeLimit = float(os.environ.get("SP_TL", "180"))
mdl.Params.MIPFocus = 1
mdl.optimize()

res = {"board": os.path.basename(prob_path), "columns": len(cols),
       "status": mdl.Status, "sol_count": mdl.SolCount}
if mdl.SolCount == 0:
    print(json.dumps(res)); raise SystemExit

flat = [0] * (n * 7)
for c, col in enumerate(cols):
    if x[c].X < 0.5:
        continue
    for i, (oi, xx, yy, e) in col["plan"].items():
        if y[i, c].X > 0.5:
            flat[i*7:i*7+7] = [col["bay"], oi, xx, yy, e, e + P[i], 1]
eng = M._build_engine(p)
hor = max(D) + max(P) + 40
eng.build_occ(hor); eng.set_solution(flat); eng.build_occ(hor)
res["recombined_obj"] = eng.objective()[3]
res["vp_violations"] = sum(1 for i in range(n)
                           if flat[i*7+6] and not eng.vp_ok(i))
best_in = None
for fb in inputs:
    eng.set_solution(fb); eng.build_occ(hor)
    v = eng.objective()[3]
    best_in = v if best_in is None else min(best_in, v)
res["best_input"] = best_in
eng.set_solution(flat); eng.build_occ(hor)
sol = M._recs_to_sol(M._extract(eng, n))
chk = M._check(p, sol)
res["checker_feasible"] = None if chk is None else bool(chk.get("feasible"))
res["checker_obj"] = None if chk is None else chk.get("objective")
if best_in:
    res["gain_vs_best_input_pct"] = round(100 * (best_in - res["recombined_obj"]) / best_in, 3)
json.dump(sol, open(out_path, "w"))
print(json.dumps(res))
