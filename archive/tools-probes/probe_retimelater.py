"""Delay-shift probe: the paradigm can never place a block LATER than its
earliest feasible slot. Test the hole on best P4: retime due-window
blockers LATER in place (position kept), then enter the tardy block on
time; judge by true objective with snapshot revert."""
import json
import math
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
flat = []
for i in range(n):
    if i in ent:
        b, o, x, y = meta[i]
        flat += [b, o, x, y, ent[i], ext.get(i, horizon), 1]
    else:
        flat += [0, 0, 0, 0, 0, 0, 0]
eng.set_solution(flat)
eng.build_occ(horizon)
inc = eng.objective()[3]
print(json.dumps({"incumbent": inc}), flush=True)
tardy = sorted((i for i in ent if ent[i] + P[i] > D[i]),
               key=lambda i: -(ent[i] + P[i] - D[i]))
total_kept = 0
for A in tardy[:8]:
    tstar = D[A] - P[A]
    if tstar < 0:
        continue
    jA = meta[A][0]
    snap = eng.get_solution()
    before = eng.objective()[3]
    blockers = [k for k in ent
                if k != A and meta[k][0] == jA
                and ent[k] < tstar + P[A]
                and ext.get(k, horizon) > tstar]
    moved = 0
    for k in blockers:
        for d in (1, 2, 3, 5, 8):
            if eng.retime_block(k, d, d):
                moved += 1
                break
    okA = eng.insert_capped(A, tstar, jA) if eng.get_block(A)[6] == 0 \
        else False
    if not okA:
        b = eng.get_block(A)
        eng.set_block(A, b[0], b[1], b[2], b[3], b[4], b[5], False)
        eng.finalize_load()
        eng.build_occ(horizon)
        okA = eng.insert_capped(A, tstar, jA)
    after = eng.objective()[3] if okA else None
    if okA and after < before:
        total_kept += 1
        print(json.dumps({"A": A, "moved_blockers": moved,
                          "before": before, "after": after}), flush=True)
    else:
        eng.set_solution(snap)
        eng.build_occ(horizon)
print(json.dumps({"kept": total_kept, "final": eng.objective()}),
      flush=True)
