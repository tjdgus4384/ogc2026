"""EXACT M1: g-scored anytime beam over EDD block order with DELAYED
entry branches. Judges LEAF boards directly vs our best anneal board
(3,244,599 P4 s7). Kill criterion: no leaf below it."""
import json
import math
import sys
import time

sys.path.insert(0, "solver")
import myalgorithm as M

prob = json.load(open("data/preliminary_test/prob_4.json"))
n = len(prob["blocks"])
D = [int(round(b["due_date"])) for b in prob["blocks"]]
order = sorted(range(n), key=lambda i: D[i])
WIDTH = int(sys.argv[1]) if len(sys.argv) > 1 else 40
DELAYS = [0, 3, 8]
eng, _, _ = M._fresh(prob)
t0 = time.time()
empty = eng.get_solution()
beam = [(0.0, empty)]
for depth, blk in enumerate(order):
    cands = []
    for g, flat in beam:
        eng.set_solution(flat)
        eng.build_occ(0)
        eng.build_occ(10 ** 6 if False else
                      max(D) + 40 + max(int(math.ceil(
                          b["processing_time"])) for b in prob["blocks"]))
        seen_e = set()
        for d in DELAYS:
            pos = eng.feasible_positions(blk, 3, 0 if d == 0 else -1)
            # d>0: recompute base e per bay then ask eMin = e0+d
            if d == 0:
                rows = [pos[r:r + 5] for r in range(0, len(pos), 5)]
            else:
                rows = []
                for r in range(0, len(pos), 5):
                    b_, o_, x_, y_, e_ = pos[r:r + 5]
                    p2 = eng.feasible_positions(blk, 1, e_ + d)
                    if p2:
                        rows.append(p2[:5])
            for b_, o_, x_, y_, e_ in rows:
                if (b_, e_) in seen_e:
                    continue
                seen_e.add((b_, e_))
                snap = eng.get_solution()
                if eng.place_at(blk, b_, o_, x_, y_, e_):
                    cands.append((eng.objective()[3], eng.get_solution()))
                    eng.set_solution(snap)
                    eng.build_occ(max(D) + 60)
    if not cands:
        print(json.dumps({"dead_at": depth}), flush=True)
        sys.exit(0)
    cands.sort(key=lambda t: t[0])
    dedup, seen = [], set()
    for g, f in cands:
        key = tuple(f)
        if key not in seen:
            seen.add(key)
            dedup.append((g, f))
        if len(dedup) >= WIDTH:
            break
    beam = dedup
    if depth % 25 == 0:
        print(json.dumps({"depth": depth, "best_g": beam[0][0],
                          "secs": round(time.time() - t0, 1)}), flush=True)
print(json.dumps({"LEAF_BEST": beam[0][0], "ref_anneal": 3244599,
                  "secs": round(time.time() - t0, 1)}), flush=True)
