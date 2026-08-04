"""Critical-block assignment BEAM probe (P4): systematically search the
bay assignments of the top-J tardiness-risk blocks (size x workload
signal carries 85% of tardiness), EDD-construct the rest free, keep the
best-B partial assignments per stage. Best leaf + 110s anneal vs plain
control."""
import json
import math
import sys
import time

sys.path.insert(0, "solver")
import myalgorithm as M

prob = json.load(open("data/preliminary_test/prob_4.json"))
n = len(prob["blocks"])
m = len(prob["bays"])
D = [int(round(b["due_date"])) for b in prob["blocks"]]
W = [b["workload"] for b in prob["blocks"]]
def ba(b):
    vs = [v for L in b["shape"][0]["layers"] for v in L]
    xs = [v[0] for v in vs]; ys = [v[1] for v in vs]
    return (max(xs) - min(xs)) * (max(ys) - min(ys))
A = [ba(b) for b in prob["blocks"]]
crit = sorted(sorted(range(n), key=lambda i: -(A[i] * W[i]))[:10],
              key=lambda i: D[i])
order = sorted(range(n), key=lambda i: D[i])
rate = M._rate(n, m)
eng, _, _ = M._fresh(prob)

def construct(asg):
    if eng.reconstruct_ordered(asg, order, False, 7) != -1:
        return None
    return eng.objective()[3]

BEAM = 8
beam = [([-1] * n, None)]
t0 = time.time()
for j in crit:
    cands = []
    for asg, _ in beam:
        for bay in range(m):
            a2 = asg[:]
            a2[j] = bay
            ob = construct(a2)
            if ob is not None:
                cands.append((ob, a2))
    cands.sort(key=lambda t: t[0])
    beam = [(a, o) for o, a in cands[:BEAM]]
print(json.dumps({"beam_secs": round(time.time() - t0, 1),
                  "leaf_objs": [round(o) for o, a in
                                [(construct(a), a) for a, _ in beam[:3]]]}),
      flush=True)

SEC = 110.0
results = []
for tag, asg in [("control", [-1] * n), ("beam", beam[0][0])]:
    e2, _, _ = M._fresh(prob)
    assert e2.reconstruct_ordered(asg, order, False, 7) == -1
    e2.run_portfolio(M._THR, SEC + 10, 1, 0.05, 0.15, 0.002, 0.9995, 7,
                     1, [e2.get_solution()], int(0.9 * SEC * rate))
    M._pref_polish(prob, e2, time.time() + 15.0)
    results.append((tag, e2.objective()))
    print(json.dumps({"tag": tag, "obj": results[-1][1]}), flush=True)
