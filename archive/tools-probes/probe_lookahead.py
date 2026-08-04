"""Placement-depth probe (P4): incremental EDD construction; at each of
the top-20 critical blocks, evaluate up to 6 feasible positions by a
1-step greedy ROLLOUT (finish construction free, objective), commit the
best; others first-feasible. Compare final construction + 110s anneal vs
plain control."""
import json
import math
import sys
import time

sys.path.insert(0, "solver")
import myalgorithm as M

prob = json.load(open("data/preliminary_test/prob_4.json"))
n = len(prob["blocks"])
D = [int(round(b["due_date"])) for b in prob["blocks"]]
W = [b["workload"] for b in prob["blocks"]]
def bba(b):
    vs = [v for L in b["shape"][0]["layers"] for v in L]
    xs = [v[0] for v in vs]; ys = [v[1] for v in vs]
    return (max(xs) - min(xs)) * (max(ys) - min(ys))
A_ = [bba(b) for b in prob["blocks"]]
crit = set(sorted(range(n), key=lambda i: -(A_[i] * W[i]))[:20])
order = sorted(range(n), key=lambda i: D[i])
rate = M._rate(n, len(prob["bays"]))

def greedy_finish(eng, rest):
    for b in rest:
        if not eng.insert_capped(b, 10 ** 9, -1):
            return None
    return eng.objective()[3]

t0 = time.time()
eng, _, _ = M._fresh(prob)
placed_prefix = []
for idx, blk in enumerate(order):
    rest = order[idx + 1:]
    if blk in crit:
        pos = eng.feasible_positions(blk, 6)
        best = None
        snap = eng.get_solution()
        for r in range(0, len(pos), 5):
            b_, o_, x_, y_, e_ = pos[r:r + 5]
            if not eng.place_at(blk, b_, o_, x_, y_, e_):
                continue
            ob = greedy_finish(eng, rest)
            if ob is not None and (best is None or ob < best[0]):
                best = (ob, (b_, o_, x_, y_, e_))
            eng.set_solution(snap)
            eng.build_occ(0)  # build_occ ignores 0? use horizon below
            eng.build_occ(max(D) + max(int(math.ceil(
                b["processing_time"])) for b in prob["blocks"]) + 40)
        if best is not None:
            eng.place_at(blk, *best[1])
            continue
    if not eng.insert_capped(blk, 10 ** 9, -1):
        print(json.dumps({"fail_at": blk}), flush=True)
        sys.exit(0)
print(json.dumps({"lookahead_secs": round(time.time() - t0, 1),
                  "construct_obj": eng.objective()[3]}), flush=True)
SEC = 110.0
eng.run_portfolio(M._THR, SEC + 10, 1, 0.05, 0.15, 0.002, 0.9995, 7, 1,
                  [eng.get_solution()], int(0.9 * SEC * rate))
M._pref_polish(prob, eng, time.time() + 15.0)
print(json.dumps({"tag": "lookahead", "obj": eng.objective()}), flush=True)
e2, _, _ = M._fresh(prob)
assert e2.reconstruct_ordered([-1] * n, order, False, 7) == -1
e2.run_portfolio(M._THR, SEC + 10, 1, 0.05, 0.15, 0.002, 0.9995, 7, 1,
                 [e2.get_solution()], int(0.9 * SEC * rate))
M._pref_polish(prob, e2, time.time() + 15.0)
print(json.dumps({"tag": "control", "obj": e2.objective()}), flush=True)
