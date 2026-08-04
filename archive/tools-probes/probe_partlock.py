"""Partial-lock paradigm probe on P3: lock only the top-K preference-
spread blocks to their argmax bay (full lock was refuted -- tardiness
explodes; partial lock preserves geometric freedom elsewhere), construct
+ 60s anneal + polish, report objective per K."""
import json
import math
import sys
import time

sys.path.insert(0, "solver")
import myalgorithm as M

prob = json.load(open("data/preliminary_test/prob_3.json"))
n = len(prob["blocks"])
pref = [b["bay_preferences"] for b in prob["blocks"]]
D = [int(round(b["due_date"])) for b in prob["blocks"]]
spread = sorted(range(n), key=lambda i: -(max(pref[i]) - min(pref[i])))
rate = M._rate(n, len(prob["bays"]))
for K in [60, 80, 100, 120]:
    eng, _, _ = M._fresh(prob)
    asg = [-1] * n
    for i in spread[:K]:
        asg[i] = max(range(len(pref[i])), key=lambda j: pref[i][j])
    order = sorted(range(n), key=lambda i: D[i])
    rc = eng.reconstruct_ordered(asg, order, False, 7)
    if rc != -1:
        print(json.dumps({"K": K, "construct_fail": rc}), flush=True)
        continue
    eng.set_assignment_lock(asg)
    core = int(0.9 * 110.0 * rate)
    eng.run_portfolio(M._THR, 120.0, 1, 0.05, 0.15, 0.002, 0.9995, 7, 1,
                      [eng.get_solution()], core)
    eng.set_assignment_lock([])
    a = eng.objective()
    M._pref_polish(prob, eng, time.time() + 30.0)
    print(json.dumps({"K": K, "after_anneal": a,
                      "after_polish": eng.objective()}), flush=True)
