"""(B) value probe: greedy chain of engine-verified preference moves on the
best P3 board. For each regretful block (by potential w3 saving), try
move_block to its most-preferred bay (tight = keep entry <= D-P); keep the
move if the TRUE objective drops. Reports the chain's total gain."""
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
z = eng.objective()
print(json.dumps({"start": z}))
cands = []
for i in range(n):
    if i not in ent:
        continue
    cur = pref[i][meta[i][0]]
    best_bay = max(range(len(pref[i])), key=lambda j: pref[i][j])
    if pref[i][best_bay] > cur:                # checker: higher pref = better
        cands.append((pref[i][best_bay] - cur, i, best_bay))
cands.sort(reverse=True)
moved = 0
for gain, i, tb in cands:
    before = eng.objective()[3]
    if eng.move_block(i, tb, True, 7):
        after = eng.objective()[3]
        if after < before:
            moved += 1
        else:
            ob = meta[i][0]
            eng.move_block(i, ob, True, 7)     # move back (best effort)
print(json.dumps({"tried": len(cands), "kept": moved,
                  "final": eng.objective()}))
