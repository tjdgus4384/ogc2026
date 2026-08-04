"""Pair-exchange value probe on the POLISHED P3 board: for each regretful
block A whose direct pref-move fails, move one blocker B out of A's
preferred bay first (B chosen by least pref-loss), retry A, keep iff the
true objective drops; else revert both."""
import json
import math
import sys

sys.path.insert(0, "solver")
import myalgorithm as M

prob = json.load(open("data/preliminary_test/prob_3.json"))
sol = json.load(open(sys.argv[1]))
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
pref = [b["bay_preferences"] for b in prob["blocks"]]
horizon = max(D) + max(P) + 40
eng = M._build_engine(prob)
eng.build_occ(horizon)
flat = []
for i in range(n):
    if i in ent:
        b, o, x, y = meta[i]
        flat += [b, o, x, y, ent[i], ext.get(i, horizon), 1]
    else:
        flat += [0, 0, 0, 0, 0, 0, 0]
eng.set_solution(flat)
eng.build_occ(horizon)
print(json.dumps({"start": eng.objective()}))

def bay_of(i):
    return eng.get_block(i)[0]

def tardy(i):
    return eng.get_block(i)[4] > D[i] - P[i]

kept = 0
for i in range(n):
    if not eng.get_block(i)[6] or tardy(i):
        continue
    cb = bay_of(i)
    tb = max(range(len(pref[i])), key=lambda j: pref[i][j])
    if tb == cb or pref[i][tb] <= pref[i][cb]:
        continue
    base = eng.objective()[3]
    if eng.move_block(i, tb, True, 7):        # direct move works?
        if eng.objective()[3] < base:
            kept += 1
            continue
        eng.move_block(i, cb, True, 7)
        continue
    blockers = [k for k in range(n)
                if k != i and eng.get_block(k)[6] and bay_of(k) == tb
                and not tardy(k)]
    blockers.sort(key=lambda k: pref[k][tb] - pref[k][cb])  # least loss first
    done = False
    for k in blockers[:10]:
        kb = bay_of(k)
        moved_k = False
        for jb in sorted(range(len(pref[k])), key=lambda j: -pref[k][j]):
            if jb != kb and eng.move_block(k, jb, True, 7):
                moved_k = True
                break
        if not moved_k:
            continue
        if eng.move_block(i, tb, True, 7):
            if eng.objective()[3] < base:
                kept += 1
                done = True
                break
            eng.move_block(i, cb, True, 7)
        eng.move_block(k, kb, True, 7)
print(json.dumps({"kept_pairs_or_moves": kept, "final": eng.objective()}))
