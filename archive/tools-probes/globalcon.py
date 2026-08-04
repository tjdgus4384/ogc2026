"""Global exact construction: place ALL n blocks in one CP-SAT solve.

Space is locally optimal (any <=30-block rearrangement, proven), time is
globally optimal at fixed geometry (timeopt.py, proven). The gap can
therefore only live in globally different geometry -- and the four
refuted construction-diversity attempts all shared one thing: the same
greedy insertion, varied only in order. This is the other construction:
enumerate candidate placements per block with the engine (biased to the
punctual window), build exact pairwise conflicts in C++, and let CP-SAT
choose one placement per block for the WHOLE board simultaneously,
minimizing the true weighted objective. vp-verify; lazy-cut on failure.

Not expected to be optimal (candidate sets are finite) -- expected to be
DIFFERENT. The measurement is whether its boards, after a standard
anneal polish, reach basins the greedy construction cannot.

Usage: globalcon.py <pnum> [ncand] [solve_secs] [anneal_secs]
"""
import json
import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "solver"))
os.chdir(ROOT)

from ortools.sat.python import cp_model
import sscore

pnum = int(sys.argv[1])
NC = int(sys.argv[2]) if len(sys.argv) > 2 else 15
STL = float(sys.argv[3]) if len(sys.argv) > 3 else 300.0
ANN = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0

prob = json.load(open(f"data/preliminary_test/prob_{pnum}.json"))
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

# candidates on an EMPTY board (construction, not repair): the engine
# enumerates vp-legal positions per block against emptiness, from the
# punctual window first. Hard blocks (few punctual slots) keep late
# fallbacks so the model always has a complete assignment available.
t0 = time.time()
CAND = {}
for i in range(n):
    got = []
    raw = eng.feasible_positions(i, NC, 0)
    got = [tuple(raw[q:q+5]) for q in range(0, len(raw), 5)]
    CAND[i] = got
sizes = [len(v) for v in CAND.values()]
print(json.dumps({"p": pnum, "cand_secs": round(time.time()-t0, 1),
                  "cand_min": min(sizes), "cand_mean": round(sum(sizes)/n, 1),
                  "cands_total": sum(sizes)}), flush=True)

flatc, idx = [], []
for i in range(n):
    for q, (j, oi, x, y, e) in enumerate(CAND[i]):
        flatc += [i, j, oi, x, y, e]
        idx.append((i, q))
t0 = time.time()
prs = eng.pair_conflicts(flatc)
print(json.dumps({"conflict_secs": round(time.time()-t0, 1),
                  "conflicts": len(prs)//2}), flush=True)

t0 = time.time()
mdl = cp_model.CpModel()
V = {i: [mdl.NewBoolVar(f"z{i}_{q}") for q in range(len(CAND[i]))]
     for i in range(n)}
for i in range(n):
    mdl.AddExactlyOne(V[i])
for t in range(0, len(prs), 2):
    (i, q), (k, r) = idx[prs[t]], idx[prs[t+1]]
    mdl.AddBoolOr([V[i][q].Not(), V[k][r].Not()])
mdl.Minimize(sum(int(W["w1"] * max(0, c[4] + P[i] - D[i])
                     + W["w3"] * (SMAX[i] - PREF[i][c[0]])) * V[i][q]
                 for i in range(n) for q, c in enumerate(CAND[i])))
print(json.dumps({"model_secs": round(time.time()-t0, 1)}), flush=True)

sv = cp_model.CpSolver()
sv.parameters.max_time_in_seconds = STL
sv.parameters.num_search_workers = 8
t0 = time.time()
st = sv.Solve(mdl)
print(json.dumps({"status": sv.StatusName(st),
                  "solve_secs": round(time.time()-t0, 1),
                  "bound": sv.BestObjectiveBound()
                  if st in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None}),
      flush=True)
if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
    raise SystemExit

new = [0] * (n * 7)
for i in range(n):
    for q, c in enumerate(CAND[i]):
        if sv.Value(V[i][q]):
            j, oi, x, y, e = c
            new[i*7:i*7+7] = [j, oi, x, y, e, e + P[i], 1]
            break
eng.set_solution(new)
eng.build_occ(HOR)
bad = [i for i in range(n) if new[i*7+6] and not eng.vp_ok(i)]
z1, z2, z3, tot = eng.objective()
print(json.dumps({"constructed_obj": tot, "z1": z1, "z2": z2, "z3": z3,
                  "vp_violations": len(bad)}), flush=True)
if ANN > 0 and not bad:
    eng.run_portfolio(4, ANN, 1, 0.05, 0.15, 0.002, 0.9997, 7, 1, [new], 0)
    z1b, z2b, z3b, tot2 = eng.objective()
    print(json.dumps({"after_anneal": tot2, "z1": z1b, "z2": z2b,
                      "z3": z3b, "anneal_secs": ANN}), flush=True)
