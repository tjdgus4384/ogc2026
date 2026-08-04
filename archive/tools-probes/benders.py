"""Logic-based-Benders v0 (matheuristic loop): Gurobi master proposes
globally-optimal bay assignments + entry schedules under an area
surrogate; the anneal engine realizes each proposal geometrically
(forcedBay + entry locks); the checker scores; best board feeds the
mipboards loop.

Master exactness: Z1 (plain tardy days), Z2 (normalized workload
imbalance), Z3 (preference regret) are all EXACT given assignment and
entries -- only geometric packability is surrogated (utilization cap
USLK per bay-day), which the slave then verifies.
Usage: benders.py <pnum> <master_sec> <slave_sec> [POOL]
"""
import json
import math
import os
import sys
import time

import gurobipy as gp
from gurobipy import GRB

pnum = int(sys.argv[1])
msec = float(sys.argv[2]) if len(sys.argv) > 2 else 300.0
ssec = float(sys.argv[3]) if len(sys.argv) > 3 else 120.0
POOL = int(sys.argv[4]) if len(sys.argv) > 4 else 8
USLK = float(os.environ.get("USLK", "0.80"))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, "solver")
import myalgorithm as M          # noqa: E402
import utils                     # noqa: E402

prob = json.load(open(f"data/preliminary_test/prob_{pnum}.json"))
B = prob["blocks"]
bays = prob["bays"]
n = len(B)
m = len(bays)
P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
R = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]
D = [int(round(b["due_date"])) for b in B]
WL = [b["workload"] for b in B]
PR = [b["bay_preferences"] for b in B]


def shoelace(pts):
    s = 0.0
    for k in range(len(pts)):
        x1, y1 = pts[k]
        x2, y2 = pts[(k + 1) % len(pts)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2


AREA = [max(shoelace(L) for L in b["shape"][0]["layers"]) for b in B]
CAP = [b["width"] * b["height"] for b in bays]
avg_area = sum(CAP) / m
U = [avg_area / c for c in CAP]

# weights from the official report (sec 2)
W1 = {1: 21622, 2: 8000, 3: 17778, 4: 13333, 5: 13333, 6: 6667}[pnum]
W2 = {1: 10, 2: 4, 3: 5, 4: 7, 5: 7, 6: 8}[pnum]
W3 = {1: 150, 2: 200, 3: 150, 4: 150, 5: 133, 6: 150}[pnum]

H = max(D) + max(P) + 20

mdl = gp.Model("master")
mdl.Params.TimeLimit = msec
mdl.Params.PoolSearchMode = 2
mdl.Params.PoolSolutions = POOL

a = mdl.addVars(n, m, vtype=GRB.BINARY, name="a")
x = mdl.addVars(n, H, vtype=GRB.BINARY, name="x")     # entry-at-t
td = mdl.addVars(n, lb=0.0, name="td")
for i in range(n):
    mdl.addConstr(gp.quicksum(a[i, b] for b in range(m)) == 1)
    mdl.addConstr(gp.quicksum(x[i, t] for t in range(H)) == 1)
    for t in range(0, R[i]):
        mdl.addConstr(x[i, t] == 0)
    for t in range(H - P[i] + 1, H):
        mdl.addConstr(x[i, t] == 0)
    ei = gp.quicksum(t * x[i, t] for t in range(H))
    mdl.addConstr(td[i] >= ei + P[i] - D[i])

# presence-in-bay products v[i,b,t] only where needed via aggregation:
# area load per (bay, day) with linearized x*a products
v = {}
for i in range(n):
    for b in range(m):
        for s in range(R[i], H - P[i] + 1):
            v[i, b, s] = mdl.addVar(vtype=GRB.CONTINUOUS, lb=0, ub=1)
            mdl.addConstr(v[i, b, s] <= x[i, s])
            mdl.addConstr(v[i, b, s] <= a[i, b])
            mdl.addConstr(v[i, b, s] >= x[i, s] + a[i, b] - 1)
for b in range(m):
    for t in range(H):
        load = gp.quicksum(
            AREA[i] * v[i, b, s]
            for i in range(n)
            for s in range(max(R[i], t - P[i] + 1),
                           min(t, H - P[i]) + 1)
            if (i, b, s) in v)
        mdl.addConstr(load <= USLK * CAP[b])

z2 = mdl.addVar(lb=0.0, name="z2")
for b1 in range(m):
    for b2 in range(b1 + 1, m):
        l1 = gp.quicksum(WL[i] * a[i, b1] for i in range(n))
        l2 = gp.quicksum(WL[i] * a[i, b2] for i in range(n))
        mdl.addConstr(z2 >= U[b1] * l1 - U[b2] * l2)
        mdl.addConstr(z2 >= U[b2] * l2 - U[b1] * l1)
reg = gp.quicksum(
    max(PR[i]) - gp.quicksum(PR[i][b] * a[i, b] for b in range(m))
    for i in range(n))
mdl.setObjective(
    W1 * gp.quicksum(td[i] for i in range(n)) + W2 * z2 + W3 * reg,
    GRB.MINIMIZE)

# warm start from incumbent
best = json.load(open(f"runs/mipboards/prob_{pnum}.sol.json"))
for t, ops in best["operations"].items():
    for op in ops:
        if op["type"] == "ENTRY":
            i = op["block_id"]
            a[i, op["bay_id"]].Start = 1.0
            if int(t) < H:
                x[i, int(t)].Start = 1.0

mdl.optimize()
print(json.dumps({"master_status": int(mdl.Status),
                  "master_obj": mdl.ObjVal if mdl.SolCount else None,
                  "pool": mdl.SolCount}), flush=True)

cur = json.load(open(f"runs/mipboards/prob_{pnum}.sol.json"))
rbest = utils.check_feasibility(prob, cur)["objective"]
print(json.dumps({"incumbent": rbest}), flush=True)

for k in range(mdl.SolCount):
    mdl.Params.SolutionNumber = k
    asg = [-1] * n
    caps = [-1] * n
    promised = mdl.PoolObjVal
    for i in range(n):
        for b in range(m):
            if a[i, b].Xn > 0.5:
                asg[i] = b
        for t in range(H):
            if x[i, t].Xn > 0.5:
                caps[i] = t
    t0 = time.time()
    eng, seeds = M._seed_free(prob, asg=asg)
    if eng is None:
        print(json.dumps({"k": k, "slave": "seed_failed"}), flush=True)
        continue
    eng.set_assignment_lock(asg)
    if os.environ.get("LOCKE", "1") == "1":
        eng.set_entry_lock(caps)
    starts = [seeds[0], []] + seeds[1:3] if seeds else []
    eng.run_portfolio(M._THR, ssec, 1, 0.05, 0.15, 0.002, 0.9995,
                      7, 1, starts, 0)
    eng.set_entry_lock([])
    eng.set_assignment_lock([])
    deadline = time.time() + 60.0
    sol, res = M._gate(prob, eng, n, deadline)
    if sol is None:
        print(json.dumps({"k": k, "promised": promised,
                          "slave": "gate_failed"}), flush=True)
        continue
    r = utils.check_feasibility(prob, sol)
    print(json.dumps({"k": k, "promised": promised,
                      "realized": r.get("objective"),
                      "feasible": r.get("feasible")}), flush=True)
    if r.get("feasible") and r["objective"] < rbest:
        rbest = r["objective"]
        json.dump(sol, open(f"runs/mipboards/prob_{pnum}.sol.json", "w"))
        print(json.dumps({"fed": rbest}), flush=True)
print(json.dumps({"final_best": rbest}), flush=True)
