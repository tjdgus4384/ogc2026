"""Schedule-side guidance probe (P4): construct with the top-K
tardiness-risk blocks (bbox area x workload signal) at the HEAD of the
insertion order (by due among themselves), rest EDD; then anneal 110s.
Control = plain EDD. Seeds 7/8."""
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
def ba(b):
    vs = [v for L in b["shape"][0]["layers"] for v in L]
    xs = [v[0] for v in vs]; ys = [v[1] for v in vs]
    return (max(xs) - min(xs)) * (max(ys) - min(ys))
A = [ba(b) for b in prob["blocks"]]
risk = sorted(range(n), key=lambda i: -(A[i] * W[i]))
rate = M._rate(n, len(prob["bays"]))
SEC = 110.0
for K in [0, 15, 25]:
    head = sorted(risk[:K], key=lambda i: D[i])
    rest = sorted([i for i in range(n) if i not in set(head)],
                  key=lambda i: D[i])
    order = head + rest
    for seed in [7, 8]:
        eng, _, _ = M._fresh(prob)
        rc = eng.reconstruct_ordered([-1] * n, order, False, 7)
        if rc != -1:
            print(json.dumps({"K": K, "seed": seed, "cfail": rc}),
                  flush=True)
            continue
        eng.run_portfolio(M._THR, SEC + 10, 1, 0.05, 0.15, 0.002, 0.9995,
                          seed, 1, [eng.get_solution()],
                          int(0.9 * SEC * rate))
        M._pref_polish(prob, eng, time.time() + 15.0)
        print(json.dumps({"K": K, "seed": seed,
                          "obj": eng.objective()}), flush=True)
