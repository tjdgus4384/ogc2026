"""Extended polish probe (snapshot-revert, tardy blocks included) on the
P5/P6 boards -- can pref-moves harvest their large Z3 shares?"""
import json
import math
import sys

sys.path.insert(0, "solver")
import myalgorithm as M

for pnum, solpath in [(5, "runs/plock6-s7/prob_5.sol.json"),
                      (6, "runs/plock6-s7/prob_6.sol.json")]:
    prob = json.load(open(f"data/preliminary_test/prob_{pnum}.json"))
    sol = json.load(open(solpath))
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
    start = eng.objective()
    kept = 0
    improved = True
    while improved:
        improved = False
        for i in range(n):
            b = eng.get_block(i)
            if not b[6]:
                continue
            cb = b[0]
            tb = max(range(len(pref[i])), key=lambda j: pref[i][j])
            if tb == cb or pref[i][tb] <= pref[i][cb]:
                continue
            snap = eng.get_solution()
            before = eng.objective()[3]
            if not eng.move_block(i, tb, False, 7):   # loose: any entry
                continue
            if eng.objective()[3] < before:
                kept += 1
                improved = True
            else:
                eng.set_solution(snap)
                eng.build_occ(horizon)
    print(json.dumps({"P": pnum, "start": start, "kept": kept,
                      "final": eng.objective()}), flush=True)
