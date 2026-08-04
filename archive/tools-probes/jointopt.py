"""Joint space-time subset re-solve -- the gap between the two proofs.

The <=30-block optimality proof used candidates whose entry day is glued
to the position (feasible_positions events); the global-time proof froze
positions. Neither ever optimized a subset's positions AND entry times
together. This does: each freed block picks a POSITION (bay,ori,x,y,
time-free, deduped from engine candidates) and an independent integer
ENTRY; any two chosen positions that conflict when coexisting must have
disjoint stays (conservative for shaft pairs -- misses some legal
overlaps, never invents illegal ones; vp re-verification remains the
authority). Loop over tardy-seeded random subsets like cpsat_loop.

Usage: jointopt.py <board.sol.json> <pnum> [K] [iters]
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

from ortools.sat.python import cp_model
import sscore

solf = sys.argv[1]
pnum = int(sys.argv[2])
K = int(sys.argv[3]) if len(sys.argv) > 3 else 15
ITERS = int(sys.argv[4]) if len(sys.argv) > 4 else 200
rng = random.Random(424242)

prob = json.load(open(f"data/preliminary_test/prob_{pnum}.json"))
board = json.load(open(solf))
B = prob["blocks"]; bays = prob["bays"]; W = prob["weights"]
n = len(B)
P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
R = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]
D = [int(round(b["due_date"])) for b in B]
PREF = [[float(v) for v in b["bay_preferences"]] for b in B]
SMAX = [max(pr) for pr in PREF]
HOR = (max(D) + max(P)) * 2 + 200

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
eng.set_solution(cur)
eng.build_occ(HOR)
base = eng.objective()[3]
best = base
print(json.dumps({"p": pnum, "incumbent": base, "K": K}), flush=True)

t_start = time.time()
improved = 0
for it in range(ITERS):
    tardy = [i for i in range(n) if cur[i*7+6] and cur[i*7+5] > D[i]]
    if not tardy:
        break
    seed_i = rng.choice(tardy)
    pool = [i for i in range(n) if cur[i*7+6] and i != seed_i]
    free = [seed_i] + rng.sample(pool, min(K - 1, len(pool)))
    part = list(cur)
    for i in free:
        part[i*7+6] = 0
    eng.set_solution(part)
    eng.build_occ(HOR)

    # time-free position menus: dedupe (bay,ori,x,y) across event days;
    # every position must be legal vs the frozen rest for SOME window --
    # the entry var decides which; rest-conflicts get cut lazily by vp.
    POS = {}
    for i in free:
        raw = eng.feasible_positions(i, 30, 0)
        ps, seen = [], set()
        for q in range(0, len(raw), 5):
            key = tuple(raw[q:q+4])
            if key not in seen:
                seen.add(key); ps.append(tuple(raw[q:q+5]))
        c0 = tuple(cur[i*7:i*7+5])
        if c0[:4] not in seen:
            ps.append(c0)
        POS[i] = ps

    # coexistence-conflict predicate between chosen positions: reuse the
    # exact pair engine with FORCED overlapping stays (equal entries).
    flatc, idx = [], []
    for i in free:
        for q, c in enumerate(POS[i]):
            flatc += [i, c[0], c[1], c[2], c[3], 0]     # entry 0 for all
            idx.append((i, q))
    prs = eng.pair_conflicts(flatc)
    conf = set()
    for t2 in range(0, len(prs), 2):
        conf.add((idx[prs[t2]], idx[prs[t2+1]]))

    mdl = cp_model.CpModel()
    V = {i: [mdl.NewBoolVar(f"z{i}_{q}") for q in range(len(POS[i]))]
         for i in free}
    E = {i: mdl.NewIntVar(R[i], HOR - P[i] - 1, f"e{i}") for i in free}
    for i in free:
        mdl.AddExactlyOne(V[i])
    for (a, b) in conf:
        (i, q), (k2, r) = a, b
        both = mdl.NewBoolVar("")
        mdl.AddBoolAnd([V[i][q], V[k2][r]]).OnlyEnforceIf(both)
        mdl.AddBoolOr([V[i][q].Not(), V[k2][r].Not()]).OnlyEnforceIf(both.Not())
        o1 = mdl.NewBoolVar("")
        mdl.Add(E[i] + P[i] <= E[k2]).OnlyEnforceIf([both, o1])
        mdl.Add(E[k2] + P[k2] <= E[i]).OnlyEnforceIf([both, o1.Not()])
    T = []
    for i in free:
        t3 = mdl.NewIntVar(0, HOR, f"t{i}")
        mdl.Add(t3 >= E[i] + P[i] - D[i])
        T.append(int(W["w1"]) * t3)
    reg = sum(int(W["w3"] * (SMAX[i] - PREF[i][c[0]])) * V[i][q]
              for i in free for q, c in enumerate(POS[i]))
    mdl.Minimize(sum(T) + reg)
    sv = cp_model.CpSolver()
    sv.parameters.max_time_in_seconds = 8.0
    sv.parameters.num_search_workers = 4
    st = sv.Solve(mdl)
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        continue
    new = list(part)
    for i in free:
        e = sv.Value(E[i])
        for q, c in enumerate(POS[i]):
            if sv.Value(V[i][q]):
                new[i*7:i*7+7] = [c[0], c[1], c[2], c[3], e, e + P[i], 1]
                break
    eng.set_solution(new)
    eng.build_occ(HOR)
    if any(new[i*7+6] and not eng.vp_ok(i) for i in range(n)):
        eng.set_solution(cur); eng.build_occ(HOR)
        continue
    v = eng.objective()[3]
    if v < best - 1e-9:
        improved += 1; best = v; cur = new
        print(json.dumps({"iter": it, "obj": v,
                          "delta_pct": round(100*(v-base)/base, 3)}), flush=True)
    else:
        eng.set_solution(cur); eng.build_occ(HOR)

print(json.dumps({"tried": ITERS, "improved": improved, "incumbent": base,
                  "final": best, "delta_pct": round(100*(best-base)/base, 3),
                  "wall": round(time.time() - t_start, 1)}), flush=True)
