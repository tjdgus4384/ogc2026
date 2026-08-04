"""Column generator for the set-partitioning master (Dantzig-Wolfe).

A column is one bay's certified plan. Harvesting only finished boards
gives m columns per board -- far too thin for the master to find an
exact partition. This generates them on purpose:

  round r:  draw a full assignment from a randomised rule
            (preference-greedy / balance-greedy / mixed, with noise)
            -> for each bay, build a ONE-BAY sub-instance of exactly
               that bay's blocks and let the engine pack it
            -> record the resulting plan as a column.

With one bay the sub-instance's Z2 vanishes and its Z3 is a constant,
so the engine minimises pure tardiness for that subset in that bay --
which is exactly the pricing question the master asks. Columns from the
same round partition by construction; the master's power is that it may
MIX rounds, reaching assignments no single run produced.

Usage: colgen.py <prob.json> <out_pool.json> [rounds] [sec_per_bay]
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
ROUNDS = int(sys.argv[3]) if len(sys.argv) > 3 else 12
SEC = float(sys.argv[4]) if len(sys.argv) > 4 else 6.0

p = json.load(open(prob_path))
B, bays, W = p["blocks"], p["bays"], p["weights"]
n, m = len(B), len(bays)
D = [int(round(b["due_date"])) for b in B]
P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
PREF = [[float(v) for v in b["bay_preferences"]] for b in B]
SMAX = [max(pr) for pr in PREF]
L = [float(b["workload"]) for b in B]
ar = [b["width"] * b["height"] for b in bays]
u = [(sum(ar) / m) / a for a in ar]

rng = random.Random(20260804)


def assignment(kind, noise):
    """randomised full assignment: preference-greedy / balance-greedy / mix"""
    load = [0.0] * m
    asg = [0] * n
    order = sorted(range(n), key=lambda i: -L[i] + rng.gauss(0, noise * 20))
    for i in order:
        if kind == "pref":
            sc = [PREF[i][j] - noise * 100 * rng.random() for j in range(m)]
            j = max(range(m), key=lambda q: sc[q])
        elif kind == "bal":
            j = min(range(m), key=lambda q: u[q] * load[q]
                    + noise * 50 * rng.random())
        else:                                   # priced mix of both
            j = max(range(m), key=lambda q: PREF[i][q]
                    - 0.02 * u[q] * load[q] + noise * 40 * rng.random())
        asg[i] = j
        load[j] += L[i]
    return asg


def pack_bay(idx, j, sec):
    """one-bay sub-instance -> (plan, tardiness) or None"""
    if not idx:
        return {}, 0.0
    sub = {"bays": [bays[j]],
           "blocks": [{**B[i], "bay_preferences": [PREF[i][j]]} for i in idx],
           "weights": W}
    eng = M._build_engine(sub)
    if eng is None:
        return None
    hor = max(int(round(B[i]["due_date"])) for i in idx) + max(P[i] for i in idx) + 40
    eng.build_occ(hor)
    k = len(idx)
    order = sorted(range(k), key=lambda q: D[idx[q]])
    if eng.reconstruct_ordered([-1] * k, order, False, 7) != -1:
        return None
    eng.run_portfolio(min(4, os.cpu_count() or 1), sec, 1, 0.05, 0.15,
                      0.002, 0.9995, 7, 1, [eng.get_solution()])
    flat = eng.get_solution()
    plan, T = {}, 0.0
    for q, i in enumerate(idx):
        if not flat[q*7+6]:
            return None
        plan[i] = (flat[q*7+1], flat[q*7+2], flat[q*7+3], flat[q*7+4])
        T += max(0, flat[q*7+5] - D[i])
    return plan, T


pool = []
t0 = time.time()
kinds = ("pref", "bal", "mix")
for r in range(ROUNDS):
    kind = kinds[r % 3]
    noise = 0.15 + 0.25 * (r // 3) / max(1, (ROUNDS // 3))
    asg = assignment(kind, noise)
    for j in range(m):
        idx = [i for i in range(n) if asg[i] == j]
        got = pack_bay(idx, j, SEC)
        if got is None:
            continue
        plan, T = got
        pool.append({"bay": j, "blocks": idx, "T": T,
                     "R": sum(SMAX[i] - PREF[i][j] for i in idx),
                     "load": sum(L[i] for i in idx),
                     "plan": {str(i): plan[i] for i in idx},
                     "src": f"{kind}{r}"})
json.dump({"instance": os.path.basename(prob_path), "columns": pool},
          open(out_path, "w"))
print(json.dumps({"instance": os.path.basename(prob_path),
                  "rounds": ROUNDS, "columns": len(pool),
                  "wall": round(time.time() - t0, 1)}))
