"""Path relinking between elite boards -- the untried joint-space operator.

Every decomposition lost information: freezing the assignment made exact
geometry worth 0.0%, freezing the geometry made assignment optimisation
worse than free search, and building from scratch was 2-3x worse than
the engine's greedy. What all three share is that they cut the joint
(assignment x geometry x time) structure somewhere. Path relinking does
not cut it -- it walks from one elite board to another THROUGH the joint
space, one block at a time, and reads the objective at every step.

Bay-plan recombination was already proven worthless (Gurobi: OPTIMAL at
0.0% over a 125-column pool), but that operated at the granularity of a
whole bay's plan. A block-level path is far finer: it can stop halfway,
where neither endpoint's structure is intact.

  relink.py <prob.json> <out.json> <boardA> <boardB> [passes]
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

prob_path, out_path = sys.argv[1], sys.argv[2]
PA, PB = sys.argv[3], sys.argv[4]
PASSES = int(sys.argv[5]) if len(sys.argv) > 5 else 6

p = json.load(open(prob_path))
B = p["blocks"]
n = len(B)
D = [int(round(b["due_date"])) for b in B]
P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
HOR = max(D) + max(P) + 40
rng = random.Random(20260804)


def load(path):
    flat = [0] * (n * 7)
    for t, day in json.load(open(path))["operations"].items():
        for o in day:
            i = o["block_id"]
            if o["type"] == "ENTRY":
                flat[i*7:i*7+5] = [o["bay_id"], o["orient_idx"], o["x"],
                                   o["y"], int(t)]
                flat[i*7+6] = 1
            else:
                flat[i*7+5] = int(t)
    return flat


A, Bf = load(PA), load(PB)
eng = M._build_engine(p)
eng.build_occ(HOR)


def obj(flat):
    eng.set_solution(flat)
    eng.build_occ(HOR)
    return eng.objective()[3]


def feasible_after(flat, i):
    """cheap guard: the moved block and everything sharing its bay"""
    eng.set_solution(flat)
    eng.build_occ(HOR)
    if not eng.vp_ok(i):
        return False
    j = flat[i*7]
    for k in range(n):
        if k != i and flat[k*7+6] and flat[k*7] == j and not eng.vp_ok(k):
            return False
    return True


t0 = time.time()
objA, objB = obj(A), obj(Bf)
best, best_flat = min((objA, "A"), (objB, "B"))[0], A if objA <= objB else Bf
res = {"board": os.path.basename(prob_path), "objA": objA, "objB": objB,
       "passes": PASSES, "steps_taken": 0, "improving_steps": 0}

for ps in range(PASSES):
    src, dst = (A, Bf) if ps % 2 == 0 else (Bf, A)
    cur = list(src)
    diff = [i for i in range(n)
            if cur[i*7+6] and dst[i*7+6] and cur[i*7:i*7+5] != dst[i*7:i*7+5]]
    rng.shuffle(diff)
    for i in diff:
        trial = list(cur)
        trial[i*7:i*7+5] = dst[i*7:i*7+5]
        trial[i*7+5] = dst[i*7+4] + P[i]
        if not feasible_after(trial, i):
            continue
        cur = trial
        res["steps_taken"] += 1
        v = obj(cur)
        if v < best:
            best, best_flat = v, list(cur)
            res["improving_steps"] += 1

res["best"] = best
res["gain_vs_best_endpoint_pct"] = round(
    100 * (min(objA, objB) - best) / min(objA, objB), 3)
res["wall"] = round(time.time() - t0, 1)
eng.set_solution(best_flat)
eng.build_occ(HOR)
sol = M._recs_to_sol(M._extract(eng, n))
chk = M._check(p, sol)
res["checker_feasible"] = None if chk is None else bool(chk.get("feasible"))
res["checker_obj"] = None if chk is None else chk.get("objective")
if chk is not None and chk.get("feasible") and out_path != "/dev/null":
    json.dump(sol, open(out_path, "w"))
print(json.dumps(res))
