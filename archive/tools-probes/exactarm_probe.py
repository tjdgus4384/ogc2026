"""Exact-arm scale probe (G004): does Gurobi buy bigger exact windows?

The slice arm's CP-SAT re-placement is the only lever that ever moved a
board by >10% (v4.0 adoption: P4 -10.6%). CP-SAT tops out near K=25-30
freed blocks (K=60 measured worse: candidate dilution + solve stalls).
Gurobi is available on titan nodes (probe 62823, WLS academic) and in
the grading env. Question: at equal wall, does (a) Gurobi at K=25 match
CP-SAT (solver dynamics), and (b) Gurobi at K=80/NCAND=40 dig deeper?

  exactarm_probe.py <pnum> <cpsat|grb> <wall_s> <K> <NCAND> [soltag]

Reads data/preliminary_test/prob_N.json + runs/<soltag>/prob_N.sol.json
(default soltag p36-v51-s7). Emits one JSON verdict line on stdout.
Judgment: same rng seed across arms -> identical first subsets; endpoint
objective decides. Kill rule: no arm beats the incumbent -> axis dead
(matches the exact-window B&B refutation); grb80 > cpsat25 by >=1% on
any board -> wire into _slice_refine and A/B at official TLs.
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
import myalgorithm as M  # noqa: E402

pnum = int(sys.argv[1])
ARM = sys.argv[2]
WALL = float(sys.argv[3])
K = int(sys.argv[4])
NCAND = int(sys.argv[5])
SOLTAG = sys.argv[6] if len(sys.argv) > 6 else "p36-v51-s7"

p = json.load(open(f"data/preliminary_test/prob_{pnum}.json"))
ops = json.load(open(f"runs/{SOLTAG}/prob_{pnum}.sol.json"))["operations"]
B = p["blocks"]; W = p["weights"]
n = len(B)
P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
D = [int(round(b["due_date"])) for b in B]
PREF = [[float(v) for v in b["bay_preferences"]] for b in B]
SMAX = [max(pr) for pr in PREF]

flat = [0] * (n * 7)
for t, day in ops.items():
    for op in day:
        i = op["block_id"]
        if op["type"] == "ENTRY":
            flat[i*7:i*7+5] = [op["bay_id"], op["orient_idx"], op["x"],
                               op["y"], int(t)]
            flat[i*7+6] = 1
        else:
            flat[i*7+5] = int(t)

eng = M._build_engine(p)
hor = max(D) + max(P) + 40
eng.build_occ(hor)
eng.set_solution(flat)
eng.build_occ(hor)
base = eng.objective()[3]
cur = list(flat)
best = base

if ARM == "grb":
    import gurobipy as gp
else:
    from ortools.sat.python import cp_model as cp

rng = random.Random(4242)
t0 = time.time()
rounds = applied = 0
while time.time() - t0 < WALL:
    rounds += 1
    tardy = [i for i in range(n) if cur[i*7+6] and cur[i*7+5] > D[i]]
    if not tardy:
        break
    seed_i = rng.choice(tardy)
    free = [seed_i]
    pool = [i for i in range(n) if cur[i*7+6] and i != seed_i]
    free += rng.sample(pool, min(K - 1, len(pool)))
    part = list(cur)
    for i in free:
        part[i*7+6] = 0
    eng.set_solution(part); eng.build_occ(hor)
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
    prs = eng.pair_conflicts(flatc)
    cost = {(i, q): (W["w1"] * max(0, c[4] + P[i] - D[i])
                     + W["w3"] * (SMAX[i] - PREF[i][c[0]]))
            for i in free for q, c in enumerate(CAND[i])}
    left = min(6.0 if ARM == "cpsat" else 10.0, WALL - (time.time() - t0))
    if left < 0.8:
        break
    pick = {}
    if ARM == "grb":
        env = gp.Env(params={"OutputFlag": 0})
        m = gp.Model(env=env)
        z = {(i, q): m.addVar(vtype="B") for i in free
             for q in range(len(CAND[i]))}
        for i in free:
            m.addConstr(gp.quicksum(z[i, q] for q in range(len(CAND[i]))) == 1)
        for t2 in range(0, len(prs), 2):
            a, b2 = idx[prs[t2]], idx[prs[t2 + 1]]
            m.addConstr(z[a] + z[b2] <= 1)
        m.setObjective(gp.quicksum(cost[k2] * z[k2] for k2 in z))
        m.Params.TimeLimit = left
        m.Params.Threads = 4
        m.Params.MIPFocus = 1
        m.optimize()
        if m.SolCount == 0:
            eng.set_solution(cur); eng.build_occ(hor); continue
        for (i, q), v in z.items():
            if v.X > 0.5:
                pick[i] = CAND[i][q]
    else:
        mdl = cp.CpModel()
        V = {i: [mdl.NewBoolVar(f"z{i}_{q}") for q in range(len(CAND[i]))]
             for i in free}
        for i in free:
            mdl.AddExactlyOne(V[i])
        for t2 in range(0, len(prs), 2):
            (a, qa), (b2, qb) = idx[prs[t2]], idx[prs[t2 + 1]]
            mdl.AddBoolOr([V[a][qa].Not(), V[b2][qb].Not()])
        mdl.Minimize(sum(int(cost[i, q]) * V[i][q]
                         for i in free for q in range(len(CAND[i]))))
        sv = cp.CpSolver()
        sv.parameters.max_time_in_seconds = left
        sv.parameters.num_search_workers = 4
        st = sv.Solve(mdl)
        if st not in (cp.OPTIMAL, cp.FEASIBLE):
            eng.set_solution(cur); eng.build_occ(hor); continue
        for i in free:
            for q in range(len(CAND[i])):
                if sv.Value(V[i][q]):
                    pick[i] = CAND[i][q]
                    break
    new = list(part)
    for i, c in pick.items():
        new[i*7:i*7+7] = [c[0], c[1], c[2], c[3], c[4], c[4] + P[i], 1]
    eng.set_solution(new); eng.build_occ(hor)
    if any(new[i*7+6] and not eng.vp_ok(i) for i in range(n)):
        eng.set_solution(cur); eng.build_occ(hor); continue
    v = eng.objective()[3]
    if v < best - 1e-9:
        best = v; cur = new; applied += 1
    else:
        eng.set_solution(cur); eng.build_occ(hor)

print(json.dumps({"prob": pnum, "arm": ARM, "K": K, "ncand": NCAND,
                  "wall": round(time.time() - t0, 1), "rounds": rounds,
                  "applied": applied, "base": base, "best": best,
                  "gain_pct": round(100 * (base - best) / base, 3)}))
