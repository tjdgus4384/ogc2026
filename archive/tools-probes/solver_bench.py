"""CP-SAT vs Gurobi on the identical sub-neighbourhood model.

The sub-model is: pick exactly one candidate placement per freed block,
subject to pairwise "not both" clauses from the exact geometry, minimise
a linear cost. That is a node-packing problem -- pure binary, no
continuous part, and an LP relaxation that hands back 0.5 everywhere
unless clique cuts are found. Clause learning should suit it better than
branch-and-bound over a weak relaxation, but that is a prediction, so
measure it.

Deployment note, independent of speed: the license here is a Gurobi WLS
(Web License Service) academic license, which checks out a token over
the network at every run. The grading server has no internet, so Gurobi
cannot ship regardless of how it performs. This benchmark is about
whether it is worth using OFFLINE, for diagnostics and bounds.

Usage: solver_bench.py <pnum> [K] [ncand]
"""
import json
import math
import os
import random
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "solver"))
os.chdir(ROOT)
os.environ.setdefault("GRB_LICENSE_FILE", os.path.join(ROOT, "gurobi.lic"))

from ortools.sat.python import cp_model
import gurobipy as gp
import sscore

pnum = int(sys.argv[1])
K = int(sys.argv[2]) if len(sys.argv) > 2 else 40
NCAND = int(sys.argv[3]) if len(sys.argv) > 3 else 40
rng = random.Random(20260803)

prob = json.load(open(f"data/preliminary_test/prob_{pnum}.json"))
board = json.load(open(f"runs/mipboards/prob_{pnum}.sol.json"))
B = prob["blocks"]; bays = prob["bays"]; W = prob["weights"]
n = len(B)
P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
R = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]
D = [int(round(b["due_date"])) for b in B]
PREF = [[float(v) for v in b["bay_preferences"]] for b in B]
SMAX = [max(pr) for pr in PREF]
HOR = max(D) + max(P) + 40

eng = sscore.Engine()
eng.init_problem(n, len(bays), R, D, P,
                 [float(b["workload"]) for b in B], PREF,
                 [float(b["width"]) for b in bays],
                 [float(b["height"]) for b in bays],
                 float(W["w1"]), float(W["w2"]), float(W["w3"]))
for i in range(n):
    for oi in range(len(B[i]["shape"])):
        L = [[list(v) for v in lay] for lay in B[i]["shape"][oi]["layers"]]
        rx, ry = L[0][0]
        eng.add_orient(i, [[(x - rx, y - ry) for (x, y) in lay] for lay in L])
eng.build_occ(HOR)

cur = [0] * (n * 7)
for t, ops in board["operations"].items():
    for op in ops:
        if op["type"] == "ENTRY":
            i = op["block_id"]; e = int(t)
            cur[i*7:i*7+7] = [op["bay_id"], op["orient_idx"], op["x"],
                              op["y"], e, e + P[i], 1]
eng.set_solution(cur); eng.build_occ(HOR)

tardy = [i for i in range(n) if cur[i*7+6] and cur[i*7+5] > D[i]]
seed_i = rng.choice(tardy)
pool = [i for i in range(n) if cur[i*7+6] and i != seed_i]
free = [seed_i] + rng.sample(pool, min(K - 1, len(pool)))
part = list(cur)
for i in free:
    part[i*7+6] = 0
eng.set_solution(part); eng.build_occ(HOR)

CAND = {}
for i in free:
    raw = eng.feasible_positions(i, NCAND, 0)
    cs = [tuple(raw[q:q+5]) for q in range(0, len(raw), 5)]
    c0 = tuple(cur[i*7:i*7+5])
    if c0 not in cs:
        cs.append(c0)
    CAND[i] = cs

flatc, idx = [], []
for i in free:
    for q, (j, oi, x, y, e) in enumerate(CAND[i]):
        flatc += [i, j, oi, x, y, e]
        idx.append((i, q))
t0 = time.time()
prs = eng.pair_conflicts(flatc)
tconf = time.time() - t0


def cost(i, c):
    j, oi, x, y, e = c
    return (W["w1"] * max(0, e + P[i] - D[i])
            + W["w3"] * (SMAX[i] - PREF[i][j]))


print(json.dumps({"p": pnum, "freed": len(free), "cands": len(idx),
                  "conflicts": len(prs)//2,
                  "conflict_build_secs": round(tconf, 2)}), flush=True)

# ---- CP-SAT
m = cp_model.CpModel()
V = {i: [m.NewBoolVar(f"z{i}_{q}") for q in range(len(CAND[i]))] for i in free}
for i in free:
    m.AddExactlyOne(V[i])
for t in range(0, len(prs), 2):
    (i, q), (k, r) = idx[prs[t]], idx[prs[t+1]]
    m.AddBoolOr([V[i][q].Not(), V[k][r].Not()])
m.Minimize(sum(cost(i, CAND[i][q]) * V[i][q]
               for i in free for q in range(len(CAND[i]))))
sv = cp_model.CpSolver()
sv.parameters.max_time_in_seconds = 120
sv.parameters.num_search_workers = 4
t0 = time.time(); st = sv.Solve(m); tcp = time.time() - t0
print(json.dumps({"solver": "cp-sat", "status": sv.StatusName(st),
                  "obj": sv.ObjectiveValue() if st in (4, 2) else None,
                  "bound": sv.BestObjectiveBound() if st in (4, 2) else None,
                  "secs": round(tcp, 2)}), flush=True)

# ---- Gurobi
try:
    g = gp.Model()
    g.setParam("OutputFlag", 0)
    g.setParam("TimeLimit", 120)
    g.setParam("Threads", 4)
    Y = {}
    for i in free:
        Y[i] = g.addVars(len(CAND[i]), vtype=gp.GRB.BINARY)
        g.addConstr(gp.quicksum(Y[i][q] for q in range(len(CAND[i]))) == 1)
    for t in range(0, len(prs), 2):
        (i, q), (k, r) = idx[prs[t]], idx[prs[t+1]]
        g.addConstr(Y[i][q] + Y[k][r] <= 1)
    g.setObjective(gp.quicksum(cost(i, CAND[i][q]) * Y[i][q]
                               for i in free for q in range(len(CAND[i]))))
    t0 = time.time(); g.optimize(); tg = time.time() - t0
    print(json.dumps({"solver": "gurobi", "status": int(g.Status),
                      "obj": g.ObjVal if g.SolCount else None,
                      "bound": g.ObjBound, "secs": round(tg, 2)}), flush=True)
except Exception as e:
    print(json.dumps({"solver": "gurobi", "error": str(e)[:200]}), flush=True)
