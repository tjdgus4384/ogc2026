"""Randomized-restart window repack probe on the best P4 board: does ANY
of ~300 random reinsertion orders of a congested window's blocks beat the
incumbent realization?"""
import json
import math
import random
import sys

sys.path.insert(0, "solver")
import myalgorithm as M

prob = json.load(open("data/preliminary_test/prob_4.json"))
sol = json.load(open("runs/plock6-s7/prob_4.sol.json"))
n = len(prob["blocks"])
ent, ext, meta = {}, {}, {}
for t, ops in sol["operations"].items():
    for op in ops:
        i = op["block_id"]
        if op["type"] == "ENTRY":
            ent[i] = int(t)
            meta[i] = (op["bay_id"], op["orient_idx"], op["x"], op["y"])
        else:
            ext[i] = int(t)
D = [int(round(b["due_date"])) for b in prob["blocks"]]
P = [max(1, int(math.ceil(b["processing_time"] - 1e-9)))
     for b in prob["blocks"]]
horizon = max(D) + max(P) + 40
eng = M._build_engine(prob)
eng.build_occ(horizon)
base_flat = []
for i in range(n):
    if i in ent:
        b, o, x, y = meta[i]
        base_flat += [b, o, x, y, ent[i], ext.get(i, horizon), 1]
    else:
        base_flat += [0, 0, 0, 0, 0, 0, 0]
eng.set_solution(base_flat)
eng.build_occ(horizon)
inc = eng.objective()[3]
print(json.dumps({"incumbent": inc}), flush=True)

# top congested (bay, 16d window) by resident count
from collections import Counter
cnt = Counter()
for i in ent:
    for w0 in range(0, horizon, 16):
        if ent[i] < w0 + 32 and ext.get(i, horizon) > w0:
            cnt[(meta[i][0], w0)] += 1
rng = random.Random(5)
for (bay, w0), _ in cnt.most_common(3):
    inside = [i for i in ent
              if meta[i][0] == bay and ent[i] >= w0
              and ext.get(i, horizon) <= w0 + 32]
    if len(inside) < 4:
        continue
    best = inc
    for trial in range(200):
        eng.set_solution(base_flat)
        eng.build_occ(horizon)
        order = inside[:]
        rng.shuffle(order)
        for i in order:
            b = eng.get_block(i)
            eng.set_block(i, b[0], b[1], b[2], b[3], b[4], b[5], False)
        eng.finalize_load()
        eng.build_occ(horizon)
        ok = True
        for i in order:
            if not eng.insert_capped(i, horizon, -1):
                ok = False
                break
        if ok:
            o = eng.objective()[3]
            if o < best:
                best = o
    print(json.dumps({"bay": bay, "w0": w0, "n_inside": len(inside),
                      "best_found": best, "improved": best < inc}),
          flush=True)
