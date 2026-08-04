"""Z1-twin of the preference lock: pin the top-K tardiness-risk blocks
(bbox x workload signal) to on-time entry (eCap = D-P) for the WHOLE
construction+anneal. K-frontier sweep on P4, seeds 7/8, 110s, vs free
control."""
import json
import math
import sys
import time

sys.path.insert(0, "solver")
import myalgorithm as M

prob = json.load(open("data/preliminary_test/prob_4.json"))
n = len(prob["blocks"])
D = [int(round(b["due_date"])) for b in prob["blocks"]]
P = [max(1, int(math.ceil(b["processing_time"] - 1e-9)))
     for b in prob["blocks"]]
W = [b["workload"] for b in prob["blocks"]]
def bba(b):
    vs = [v for L in b["shape"][0]["layers"] for v in L]
    xs = [v[0] for v in vs]; ys = [v[1] for v in vs]
    return (max(xs) - min(xs)) * (max(ys) - min(ys))
A_ = [bba(b) for b in prob["blocks"]]
risk = sorted(range(n), key=lambda i: -(A_[i] * W[i]))
order = sorted(range(n), key=lambda i: D[i])
rate = M._rate(n, len(prob["bays"]))
SEC = 110.0
for K in [0, 10, 20, 35]:
    caps = [-1] * n
    for i in risk[:K]:
        caps[i] = D[i] - P[i]
    for seed in [7, 8]:
        eng, _, _ = M._fresh(prob)
        eng.set_entry_lock(caps)
        rc = eng.reconstruct_ordered([-1] * n, order, False, 7)
        if rc != -1:
            eng.set_entry_lock([])
            print(json.dumps({"K": K, "seed": seed, "cfail": rc}),
                  flush=True)
            continue
        eng.run_portfolio(M._THR, SEC + 10, 1, 0.05, 0.15, 0.002, 0.9995,
                          seed, 1, [eng.get_solution()],
                          int(0.9 * SEC * rate))
        eng.set_entry_lock([])
        M._pref_polish(prob, eng, time.time() + 15.0)
        print(json.dumps({"K": K, "seed": seed,
                          "obj": eng.objective()}), flush=True)
