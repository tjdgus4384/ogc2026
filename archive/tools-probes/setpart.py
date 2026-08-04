"""Set-partitioning recombination of bay plans (Dantzig-Wolfe master).

The structure the measurements pinned down: with the ASSIGNMENT fixed
the objective decomposes exactly by bay (Z2/Z3 are assignment-only, Z1
is a per-block sum) and every checker rule is bay-internal (invariant
6). So a "column" = one bay's complete, already-certified plan (which
blocks it holds, where, when), and a board is any choice of one column
per bay whose block sets partition N. Merging columns taken from
DIFFERENT runs is therefore exact, not heuristic -- and it reaches
boards no single run produced, which is precisely what every local
move class provably cannot do here (single/2-exchange, exact re-place
at K<=120, whole-bay re-solve: all measured 0).

Master (Gurobi, exact):
    min  sum_c (w1*T_c + w3*R_c) x_c + w2*(zmax - zmin) + BIG*sum_i s_i
    s.t. sum_{c in bay j} x_c = 1                        for each bay j
         sum_{c: i in S_c} x_c + s_i = 1                 for each block i
         zmax >= sum_{c in bay j} u_j*L_c*x_c            for each bay j
         zmin <= sum_{c in bay j} u_j*L_c*x_c            for each bay j
s_i is an uncovered-block slack: it keeps the model feasible when the
harvested pool cannot partition exactly, and its count tells us whether
the pool needs more columns (the honest failure mode of pure
recombination) rather than silently returning a broken board.

Usage: setpart.py <prob.json> <out.json> <sol.json> [sol.json ...]
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
boards = sys.argv[3:]

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


def load_board(path):
    ops = json.load(open(path))["operations"]
    flat = [0] * (n * 7)
    for t, day in ops.items():
        for o in day:
            i = o["block_id"]
            if o["type"] == "ENTRY":
                flat[i*7:i*7+5] = [o["bay_id"], o["orient_idx"], o["x"],
                                   o["y"], int(t)]
                flat[i*7+6] = 1
            else:
                flat[i*7+5] = int(t)
    return flat


cols = []          # (bay, frozenset(blocks), cost, load, {i: (ori,x,y,e)})
seen = set()
base_objs = []
for path in boards:
    flat = load_board(path)
    for j in range(m):
        S, plan, T, R, ld = [], {}, 0.0, 0.0, 0.0
        for i in range(n):
            if flat[i*7+6] and flat[i*7] == j:
                S.append(i)
                plan[i] = (flat[i*7+1], flat[i*7+2], flat[i*7+3], flat[i*7+4])
                T += max(0, flat[i*7+5] - D[i])
                R += SMAX[i] - PREF[i][j]
                ld += L[i]
        key = (j, frozenset(S), round(T), round(R))
        if key in seen:
            continue
        seen.add(key)
        cols.append((j, frozenset(S), w1 * T + w3 * R, ld, plan))
    base_objs.append((path, flat))

BIG = w1 * (max(D) + max(P))          # an uncovered block is never worth it
mdl = gp.Model(env=gp.Env(params={"OutputFlag": 0}))
x = [mdl.addVar(vtype=GRB.BINARY) for _ in cols]
s = [mdl.addVar(vtype=GRB.BINARY) for _ in range(n)]
zmax = mdl.addVar(lb=0.0)
zmin = mdl.addVar(lb=0.0)
for j in range(m):
    idx = [c for c in range(len(cols)) if cols[c][0] == j]
    mdl.addConstr(gp.quicksum(x[c] for c in idx) == 1)
    ld = gp.quicksum(u[j] * cols[c][3] * x[c] for c in idx)
    mdl.addConstr(zmax >= ld)
    mdl.addConstr(zmin <= ld)
for i in range(n):
    idx = [c for c in range(len(cols)) if i in cols[c][1]]
    mdl.addConstr(gp.quicksum(x[c] for c in idx) + s[i] == 1)
mdl.setObjective(gp.quicksum(cols[c][2] * x[c] for c in range(len(cols)))
                 + w2 * (zmax - zmin)
                 + BIG * gp.quicksum(s), GRB.MINIMIZE)
mdl.Params.TimeLimit = float(os.environ.get("SP_TL", "120"))
mdl.Params.MIPFocus = 1
mdl.optimize()

res = {"board": os.path.basename(prob_path), "columns": len(cols),
       "boards_in": len(boards), "status": mdl.Status}
if mdl.SolCount == 0:
    print(json.dumps(res)); raise SystemExit
chosen = [c for c in range(len(cols)) if x[c].X > 0.5]
uncovered = [i for i in range(n) if s[i].X > 0.5]
res["uncovered"] = len(uncovered)

flat = [0] * (n * 7)
for c in chosen:
    j, S, _, _, plan = cols[c]
    for i in S:
        oi, xx, yy, e = plan[i]
        flat[i*7:i*7+7] = [j, oi, xx, yy, e, e + P[i], 1]
eng = M._build_engine(p)
hor = max(D) + max(P) + 40
eng.build_occ(hor)
eng.set_solution(flat)
eng.build_occ(hor)
res["recombined_obj"] = eng.objective()[3] if not uncovered else None
res["inputs"] = {}
best_in = None
for path, fb in base_objs:
    eng.set_solution(fb); eng.build_occ(hor)
    v = eng.objective()[3]
    res["inputs"][os.path.basename(path)] = v
    best_in = v if best_in is None else min(best_in, v)
res["best_input"] = best_in
if not uncovered:
    eng.set_solution(flat); eng.build_occ(hor)
    sol = M._recs_to_sol(M._extract(eng, n))
    chk = M._check(p, sol)
    res["checker_feasible"] = None if chk is None else bool(chk.get("feasible"))
    res["checker_obj"] = None if chk is None else chk.get("objective")
    res["gain_vs_best_input_pct"] = round(
        100 * (best_in - res["recombined_obj"]) / best_in, 3)
    json.dump(sol, open(out_path, "w"))
print(json.dumps(res))
