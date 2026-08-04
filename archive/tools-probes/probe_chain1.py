"""Minimal-ejection chain probe (depth 1) on best P4: for each top tardy
block A, try removing ONE resident B (any bay), then insert A on time
anywhere, then relocate B freely; keep iff objective improves
(snapshot-revert). The cascade probe evicted ALL blockers; this tests
minimal ejections."""
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
               key=lambda i: -(ent[i] + P[i] - D[i]))[:6]
kept = 0
for A in tardy:
    tstar = D[A] - P[A]
    if tstar < 0:
        continue
    # candidate ejectees: residents overlapping A's due window, any bay
    cands = [k for k in ent if k != A
             and ent[k] < tstar + P[A] and ext.get(k, horizon) > tstar]
    cands.sort(key=lambda k: (meta[k][0] != meta[A][0],))
    improved = False
    for B in cands[:25]:
        snap = eng.get_solution()
        before = eng.objective()[3]
        bA = eng.get_block(A)
        eng.set_block(A, bA[0], bA[1], bA[2], bA[3], bA[4], bA[5], False)
        bB = eng.get_block(B)
        eng.set_block(B, bB[0], bB[1], bB[2], bB[3], bB[4], bB[5], False)
        eng.finalize_load()
        eng.build_occ(horizon)
        ok = eng.insert_capped(A, tstar, -1) \
            and eng.insert_capped(B, horizon, -1)
        after = eng.objective()[3] if ok else None
        if ok and after < before:
            kept += 1
            improved = True
            print(json.dumps({"A": A, "B": B, "before": before,
                              "after": after}), flush=True)
            break
        eng.set_solution(snap)
        eng.build_occ(horizon)
print(json.dumps({"kept": kept, "final": eng.objective()}), flush=True)
