"""Value probe: alternate anneal <-> pref-polish from the polished P3
board. If re-annealing the polished basin then re-polishing descends
further, the alternation is a real optimization loop (complementary
neighborhoods)."""
import json
import math
import sys
import time

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
print(json.dumps({"start": eng.objective()}), flush=True)
rate = M._rate(n, len(prob["bays"]))
for it in range(4):
    core = int(0.9 * 55.0 * rate)
    eng.run_portfolio(M._THR, 60.0, 1, 0.05, 0.15, 0.002, 0.9995,
                      7 + it, 1, [eng.get_solution()], core)
    a = eng.objective()[3]
    M._pref_polish(prob, eng, time.time() + 30.0)
    print(json.dumps({"iter": it, "after_anneal": a,
                      "after_polish": eng.objective()[3]}), flush=True)
