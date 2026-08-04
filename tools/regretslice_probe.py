"""Regret-seeded exact-arm KILL PROBE (campaign 2, G002 axis A).

Structural gap found by decomposition (tf-v51): the slice arm's CP-SAT
re-placement only fires on TARDY-seeded subsets, so on z3dom/punctual
boards (train_final prob_21/27/34/35: Z3 share 40-92%) the exact arm is
idle. This probe runs the SAME exact re-placement machinery with
REGRET seeding (max w3*(Smax-pref) blocks) on frozen v5.1 boards and
measures the harvest.

  regretslice_probe.py <board.json> <sol.json> [wall] [K] [NCAND]

Kill rule: < 2% objective gain on every class board -> axis dead
(consistent with compactPref single-move exhaustion). Signal: >= 5%
anywhere -> integrate as z3dom-gated seeding in _slice_refine and run
the 40-pair judgment.
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
from ortools.sat.python import cp_model as cp  # noqa: E402

prob_path, sol_path = sys.argv[1], sys.argv[2]
WALL = float(sys.argv[3]) if len(sys.argv) > 3 else 120.0
K = int(sys.argv[4]) if len(sys.argv) > 4 else 25
NCAND = int(sys.argv[5]) if len(sys.argv) > 5 else 30

p = json.load(open(prob_path))
ops = json.load(open(sol_path))["operations"]
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

def regret(i, fl):
    return W["w3"] * (SMAX[i] - PREF[i][fl[i*7]])

rng = random.Random(4242)
t0 = time.time()
rounds = applied = 0
while time.time() - t0 < WALL:
    rounds += 1
    # seed on the highest-regret placed blocks (top-8 draw), subset =
    # seed + residents of its PREFERRED bay in its window + random fill
    reg = sorted((i for i in range(n) if cur[i*7+6] and regret(i, cur) > 0),
                 key=lambda i: -regret(i, cur))
    if not reg:
        break
    seed_i = reg[rng.randrange(min(8, len(reg)))]
    want = max(range(len(PREF[seed_i])), key=lambda j: PREF[seed_i][j])
    e0, f0 = cur[seed_i*7+4], cur[seed_i*7+5]
    nb = [i for i in range(n)
          if i != seed_i and cur[i*7+6] and cur[i*7] == want
          and cur[i*7+4] < f0 + P[seed_i] and cur[i*7+5] > e0 - P[seed_i]]
    nb.sort(key=lambda i: abs(cur[i*7+4] - e0))
    free = [seed_i] + nb[:K - 1]
    pool = [i for i in range(n) if cur[i*7+6] and i not in set(free)]
    if len(free) < K:
        free += rng.sample(pool, min(K - len(free), len(pool)))
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
    mdl = cp.CpModel()
    V = {i: [mdl.NewBoolVar(f"z{i}_{q}") for q in range(len(CAND[i]))]
         for i in free}
    for i in free:
        mdl.AddExactlyOne(V[i])
    for t2 in range(0, len(prs), 2):
        (a, qa), (b2, qb) = idx[prs[t2]], idx[prs[t2 + 1]]
        mdl.AddBoolOr([V[a][qa].Not(), V[b2][qb].Not()])
    mdl.Minimize(sum(int(W["w1"] * max(0, c[4] + P[i] - D[i])
                         + W["w3"] * (SMAX[i] - PREF[i][c[0]])) * V[i][q]
                     for i in free for q, c in enumerate(CAND[i])))
    sv = cp.CpSolver()
    sv.parameters.max_time_in_seconds = min(6.0, WALL - (time.time() - t0))
    sv.parameters.num_search_workers = 4
    st = sv.Solve(mdl)
    if st not in (cp.OPTIMAL, cp.FEASIBLE):
        eng.set_solution(cur); eng.build_occ(hor); continue
    new = list(part)
    for i in free:
        for q, c in enumerate(CAND[i]):
            if sv.Value(V[i][q]):
                new[i*7:i*7+7] = [c[0], c[1], c[2], c[3], c[4],
                                  c[4] + P[i], 1]
                break
    eng.set_solution(new); eng.build_occ(hor)
    if any(new[i*7+6] and not eng.vp_ok(i) for i in range(n)):
        eng.set_solution(cur); eng.build_occ(hor); continue
    v = eng.objective()[3]
    if v < best - 1e-9:
        best = v; cur = new; applied += 1
    else:
        eng.set_solution(cur); eng.build_occ(hor)

print(json.dumps({"board": os.path.basename(sol_path), "K": K,
                  "rounds": rounds, "applied": applied,
                  "base": base, "best": best,
                  "gain_pct": round(100 * (base - best) / base, 3),
                  "wall": round(time.time() - t0, 1)}))
