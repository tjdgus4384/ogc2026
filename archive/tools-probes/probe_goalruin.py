"""Offline value probe for a goal-directed ruin op: on the best P4 board,
evict the due-window blockers of one overdue big block A, insert A at its
due-feasible entry, then greedily re-place the evicted. Reports whether A
gets in on time and the induced displacement cost. Cheap decision data
BEFORE building any engine op."""
import json
import math
import sys

sys.path.insert(0, "solver")
import myalgorithm as M

prob = json.load(open("data/preliminary_test/prob_4.json"))
sol = json.load(open("runs/shipcheck-v31d12/prob_4.sol.json"))
targets = [int(x) for x in sys.argv[1:]] or [88, 3, 2]
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

for A in targets:
    tstar = D[A] - P[A]
    jA = meta[A][0]
    evict = [k for k in ent
             if k != A and meta[k][0] == jA
             and ent[k] < tstar + P[A] and ext.get(k, horizon) > tstar]
    flat = []
    for i in range(n):
        if i in ent and i != A and i not in evict:
            b, o, x, y = meta[i]
            flat += [b, o, x, y, ent[i], ext.get(i, horizon), 1]
        else:
            flat += [0, 0, 0, 0, 0, 0, 0]
    eng.set_solution(flat)
    eng.build_occ(horizon)
    okA = eng.insert_capped(A, tstar, jA)
    gotA = eng.get_block(A) if okA else None
    induced = []
    if okA:
        for k in sorted(evict, key=lambda k: D[k]):
            ok = eng.insert_capped(k, horizon, -1)
            if ok:
                bk = eng.get_block(k)
                induced.append([k, bk[4] - ent[k],
                                max(0, bk[4] + P[k] - D[k])
                                - max(0, ent[k] + P[k] - D[k])])
            else:
                induced.append([k, None, None])
    print(json.dumps({"A": A, "tstar": tstar, "bay": jA,
                      "evicted": len(evict), "A_in_time": bool(okA),
                      "A_entry": gotA[4] if gotA else None,
                      "A_tardy_before": max(0, ent[A] + P[A] - D[A]),
                      "induced_shifts": induced}))
