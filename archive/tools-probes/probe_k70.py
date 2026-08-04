"""K=35% (70 blocks) full-core across seeds 7/8/9: does the middle K keep
the 82-84k gains with Z1=0 margin on ALL seeds?"""
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
order = sorted(range(n), key=lambda i: D[i])
SEC = 223.0
for K in [int(n * 0.35)]:
    asg = [-1] * n
    for i in spread[:K]:
        asg[i] = max(range(len(pref[i])), key=lambda j: pref[i][j])
    for seed in [7, 8, 9]:
        eng, _, _ = M._fresh(prob)
        assert eng.reconstruct_ordered(asg, order, False, 7) == -1
        eng.set_assignment_lock(asg)
        eng.run_portfolio(M._THR, SEC, 1, 0.05, 0.15, 0.002, 0.9995,
                          seed, 1, [eng.get_solution()],
                          int(0.9 * SEC * rate))
        eng.set_assignment_lock([])
        M._pref_polish(prob, eng, time.time() + 20.0)
        print(json.dumps({"K": K, "seed": seed,
                          "obj": eng.objective()}), flush=True)
