"""Bay-beam axis KILL PROBE (campaign 2, G001 stage 1).

Question: with the ASSIGNMENT LOCKED to the v5.1 fixed point, can the
order-space decoder (reconstruct_ordered = greedy earliest-entry,
polygon-exact) even REPRESENT boards at the fixed point's quality?

  decode(incumbent-entry-order) vs incumbent  = representability bound:
    any beam/hill-climb over orders is bounded by what the decoder can
    realize from SOME order. If even the incumbent's own order decodes
    far above the incumbent board, order-space search over this decoder
    class is dead (matches the June constructor law: greedy realizers
    sit far above anneal fixed points).
  best over {incumbent-order, EDD, slack, hardest-first, N noisy
    perturbations} = cheap search signal on top of the bound.

Usage: bayorder_probe.py <pnum> [nsamples] [soltag]
Emits one JSON line. Kill rule: decode_gap > +5% on all boards -> axis
dies at stage 1 (record and stop). Signal: decode_gap <= ~2% anywhere
-> a kernel beam with richer entry choice gets stage 2.
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

pnum = int(sys.argv[1])
NS = int(sys.argv[2]) if len(sys.argv) > 2 else 300
SOLTAG = sys.argv[3] if len(sys.argv) > 3 else "p36-v51-s7"

p = json.load(open(f"data/preliminary_test/prob_{pnum}.json"))
ops = json.load(open(f"runs/{SOLTAG}/prob_{pnum}.sol.json"))["operations"]
B = p["blocks"]
n = len(B)
D = [int(round(b["due_date"])) for b in B]
P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
R = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]

flat = [0] * (n * 7)
for t, day in ops.items():
    for op in day:
        i = op["block_id"]
        if op["type"] == "ENTRY":
            flat[i*7:i*7+5] = [op["bay_id"], op["orient_idx"], op["x"],
                               op["y"], int(t)]
            flat[i*7+6] = 1
        else:
            flat[i*7+5] = int(t)

eng = M._build_engine(p)
hor = max(D) + max(P) + 40
eng.build_occ(hor)
eng.set_solution(flat)
eng.build_occ(hor)
obj0 = eng.objective()[3]
asg = [flat[i*7] for i in range(n)]
ent = [flat[i*7+4] for i in range(n)]

def decode(order):
    if eng.reconstruct_ordered(asg, list(order), False, 7) == -1:
        return eng.objective()[3]
    return float("inf")

t0 = time.time()
inc_order = sorted(range(n), key=lambda i: (ent[i], D[i]))
d_inc = decode(inc_order)
d_edd = decode(sorted(range(n), key=lambda i: D[i]))
d_slk = decode(sorted(range(n), key=lambda i: D[i] - R[i] - P[i]))

def area(i):
    sh = B[i]["shape"][0]["layers"]
    xs = [v[0] for lay in sh for v in lay]; ys = [v[1] for lay in sh for v in lay]
    return (max(xs) - min(xs)) * (max(ys) - min(ys))
d_hard = decode(sorted(range(n), key=lambda i: -area(i) * P[i]))

rng = random.Random(31)
best_s = min(d_inc, d_edd, d_slk, d_hard)
cur = list(inc_order)
cur_v = d_inc
for _ in range(NS):
    cand = list(cur)
    for _2 in range(1 + rng.randrange(3)):
        a = rng.randrange(n)
        b2 = min(n - 1, max(0, a + rng.randint(-10, 10)))
        cand[a], cand[b2] = cand[b2], cand[a]
    v = decode(cand)
    if v < cur_v:                       # hill-climb around the incumbent order
        cur, cur_v = cand, v
    if v < best_s:
        best_s = v

print(json.dumps({
    "prob": pnum, "incumbent": obj0,
    "decode_incumbent_order": d_inc,
    "decode_gap_pct": round(100 * (d_inc - obj0) / obj0, 2),
    "edd": d_edd, "slack": d_slk, "hard": d_hard,
    "best_sampled": best_s,
    "best_gap_pct": round(100 * (best_s - obj0) / obj0, 2),
    "samples": NS, "wall": round(time.time() - t0, 1)}))
