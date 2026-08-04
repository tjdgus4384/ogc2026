"""Mid-anneal unlock probe: K=40% locks + OGC_UNLOCKF release. Does s7
avoid the cliff while keeping 82,615-class basins?"""
import json
import math
import os
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
asg = [-1] * n
for i in spread[:int(n * 0.4)]:
    asg[i] = max(range(len(pref[i])), key=lambda j: pref[i][j])
SEC = 223.0
for seed in [7, 8, 9]:
    eng, _, _ = M._fresh(prob)
    assert eng.reconstruct_ordered(asg, order, False, 7) == -1
    eng.set_assignment_lock(asg)
    eng.run_portfolio(M._THR, SEC, 1, 0.05, 0.15, 0.002, 0.9995, seed,
                      1, [eng.get_solution()], int(0.9 * SEC * rate))
    eng.set_assignment_lock([])
    M._pref_polish(prob, eng, time.time() + 20.0)
    print(json.dumps({"unlockF": os.environ.get("OGC_UNLOCKF"),
                      "seed": seed, "obj": eng.objective()}), flush=True)
