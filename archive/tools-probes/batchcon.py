"""Batch-exact construction: build the board 20 blocks at a time, each
batch placed OPTIMALLY against the frozen prefix.

Greedy construction seats one block at a time at the earliest first-fit;
every refuted diversity attempt kept that primitive and only reordered
it. This replaces the primitive: blocks arrive in due-date order, but
each batch of K is placed by CP-SAT over engine candidates (punctual
window first), minimizing the true weighted tardiness+regret of the
batch. The whole-board exact model (globalcon) died INFEASIBLE because
empty-board candidates cluster at the same corners; here candidates are
generated against the growing prefix, so the clustering never happens.

Probe: construct, report Z components, then anneal-polish the result
with the remaining budget and compare against the same budget spent on
the shipped pipeline's own construction+anneal.

Usage: batchcon.py <pnum> <K> <anneal_secs>
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
K = int(sys.argv[2]) if len(sys.argv) > 2 else 20
ANN = float(sys.argv[3]) if len(sys.argv) > 3 else 300.0
NCAND = 30

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

order = sorted(range(n), key=lambda i: (D[i], -P[i]))
cur = [0] * (n * 7)
t0 = time.time()
placed_ct = 0
for b0 in range(0, n, K):
    batch = order[b0:b0 + K]
    eng.set_solution(cur)
    eng.build_occ(HOR)
    CAND = {}
    for i in batch:
        # EXPLICIT ENTRY-DAY LADDER. feasible_positions is repair-shaped:
        # it derives entry days from residents' exit events, so on a
        # near-empty prefix every candidate lands at e=R on the same few
        # corners and the exactly-one model goes INFEASIBLE (this was
        # the globalcon failure too, and the unchecked fallback is what
        # produced the 150/150-violation garbage boards). Construction
        # needs the days spelled out: a rung every ~P/2 across
        # [R, punctual] plus a few late rungs as pressure valves.
        cs = []
        seen = set()
        punct = max(R[i], D[i] - P[i])
        step = max(1, P[i] // 2)
        rungs = list(range(R[i], punct + 1, step))[:8]
        if punct not in rungs:
            rungs.append(punct)
        rungs += [punct + step, punct + 2 * step]
        for e0 in rungs:
            raw = eng.feasible_positions(i, 4, e0)
            for q in range(0, len(raw), 5):
                c = tuple(raw[q:q+5])
                if c not in seen:
                    seen.add(c); cs.append(c)
        CAND[i] = cs
    if any(not CAND[i] for i in batch):
        print(json.dumps({"batch": b0, "note": "no candidates for some"}),
              flush=True)
    flatc, idx = [], []
    for i in batch:
        for q, (j, oi, x, y, e) in enumerate(CAND[i]):
            flatc += [i, j, oi, x, y, e]
            idx.append((i, q))
    prs = eng.pair_conflicts(flatc)
    mdl = cp_model.CpModel()
    V = {}
    SKIP = {}
    for i in batch:
        if not CAND[i]:
            continue
        V[i] = [mdl.NewBoolVar(f"z{i}_{q}") for q in range(len(CAND[i]))]
        # ELASTIC: a due-date-ordered batch is 20 direct competitors for
        # the same windows; forcing all of them into their candidate
        # lists made every batch INFEASIBLE. A skip slack priced at one
        # full extra tardiness lets the model place what fits well and
        # hand the overflow to the uncapped greedy pass afterwards.
        SKIP[i] = mdl.NewBoolVar(f"s{i}")
        mdl.AddExactlyOne(V[i] + [SKIP[i]])
    for t in range(0, len(prs), 2):
        (i, q), (k2, r) = idx[prs[t]], idx[prs[t+1]]
        mdl.AddBoolOr([V[i][q].Not(), V[k2][r].Not()])
    mdl.Minimize(sum(int(W["w1"] * max(0, c[4] + P[i] - D[i])
                         + W["w3"] * (SMAX[i] - PREF[i][c[0]])) * V[i][q]
                     for i in V for q, c in enumerate(CAND[i]))
                 + sum(int(W["w1"] * (P[i] + 5)) * SKIP[i] for i in SKIP))
    sv = cp_model.CpSolver()
    sv.parameters.max_time_in_seconds = 10.0
    sv.parameters.num_search_workers = 4
    st = sv.Solve(mdl)
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        # SOUND fallback: sequential vp-checked insertion, never blind
        print(json.dumps({"batch": b0, "note": "INFEASIBLE, greedy"}),
              flush=True)
        eng.set_solution(cur); eng.build_occ(HOR)
        for i in batch:
            done = False
            for c in CAND[i]:
                if eng.place_at(i, c[0], c[1], c[2], c[3], c[4]):
                    cur[i*7:i*7+7] = [c[0], c[1], c[2], c[3], c[4],
                                      c[4] + P[i], 1]
                    placed_ct += 1; done = True; break
            if not done:
                print(json.dumps({"unplaced": i}), flush=True)
        continue
    for i in V:
        for q, c in enumerate(CAND[i]):
            if sv.Value(V[i][q]):
                cur[i*7:i*7+7] = [c[0], c[1], c[2], c[3], c[4],
                                  c[4] + P[i], 1]
                placed_ct += 1
                break
    # per-batch audit: find where infeasibility first enters
    eng.set_solution(cur)
    eng.build_occ(HOR)
    badb = [i for i in range(n) if cur[i*7+6] and not eng.vp_ok(i)]
    print(json.dumps({"batch": b0, "placed": placed_ct,
                      "viol_so_far": len(badb),
                      "viol_sample": badb[:5]}), flush=True)
# uncapped greedy completion for everything the elastic models skipped
eng.set_solution(cur)
eng.build_occ(HOR)
left = [i for i in order if not cur[i*7+6]]
for i in left:
    if eng.insert_capped(i, -1, -1):
        got = eng.get_solution()
        cur[i*7:i*7+7] = got[i*7:i*7+7]
        placed_ct += 1
print(json.dumps({"greedy_completed": len(left)}), flush=True)
eng.set_solution(cur)
eng.build_occ(HOR)
bad = [i for i in range(n) if cur[i*7+6] and not eng.vp_ok(i)]
z1, z2, z3, tot = eng.objective()
print(json.dumps({"constructed": placed_ct, "n": n, "z1": z1, "z2": z2,
                  "z3": z3, "obj": tot, "vp_violations": len(bad),
                  "construct_secs": round(time.time() - t0, 1)}), flush=True)
if ANN > 0 and placed_ct == n and not bad:
    eng.run_portfolio(4, ANN, 1, 0.05, 0.15, 0.002, 0.9997, 7, 1, [cur], 0)
    z1b, z2b, z3b, tot2 = eng.objective()
    print(json.dumps({"after_anneal": tot2, "z1": z1b,
                      "anneal_secs": ANN}), flush=True)
