"""Decisive P4 probe: did an EXACT feasible earlier entry exist for the
top frag_diag offenders, given the rest of the best board? usage:
    python tools/probe_entry.py <prob.json> <sol.json> <blk> [<blk> ...]
Run under OGC_STRIDE=1 (exhaustive phase-3) on a compute node.
YES(t') -> ALNS search failure (candidate: entry-directed exact scan).
NO -> area bound loose; geometric ceiling is real for that block.
"""
import json
import math
import sys

sys.path.insert(0, "solver")
import myalgorithm as M

prob = json.load(open(sys.argv[1]))
sol = json.load(open(sys.argv[2]))
targets = [int(x) for x in sys.argv[3:]]
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
R = [int(math.ceil(b["release_time"] - 1e-9)) for b in prob["blocks"]]
horizon = max(D) + max(P) + 40

eng = M._build_engine(prob)
eng.build_occ(horizon)
for A in targets:
    flat = []
    for i in range(n):
        if i in ent and i != A:
            b, o, x, y = meta[i]
            flat += [b, o, x, y, ent[i], ext.get(i, horizon), 1]
        else:
            flat += [0, 0, 0, 0, 0, 0, 0]
    eng.set_solution(flat)
    eng.build_occ(horizon)          # restamp occupancy for this board
    cur = ent.get(A)
    ok = eng.insert_capped(A, cur - 1, -1)
    got = eng.get_block(A) if ok else None
    print(json.dumps({"blk": A, "cur_e": cur, "R": R[A], "due": D[A],
                      "earlier_feasible": bool(ok),
                      "found": got}))
