"""Does the per-bay sub-anneal actually search? Perturb the start
(shift every entry +5 days) and see whether it recovers."""
import json, math, os, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT); sys.path.insert(0, "solver")
import myalgorithm as M, baysplit as BS
pnum = int(sys.argv[1]); secs = float(sys.argv[2])
prob = json.load(open(f"data/preliminary_test/prob_{pnum}.json"))
B = prob["blocks"]; n = len(B)
P = [max(1, int(math.ceil(b["processing_time"]-1e-9))) for b in B]
brd = json.load(open(f"runs/mipboards/prob_{pnum}.sol.json"))
flat = [0,0,0,0,0,0,0]*n
for t, ops in brd["operations"].items():
    for op in ops:
        if op["type"] == "ENTRY":
            i = op["block_id"]; e = int(t)
            flat[i*7:i*7+7] = [op["bay_id"], op["orient_idx"], op["x"],
                               op["y"], e, e+P[i], 1]
by = {}
for i in range(n): by.setdefault(flat[i*7], []).append(i)
bay = sorted(by)[0]; blocks = by[bay]
sub = BS._sub_problem(prob, blocks, bay)
rows_ok = [(k, flat[i*7+1], flat[i*7+2], flat[i*7+3], flat[i*7+4])
           for k, i in enumerate(blocks)]
rows_bad = [(k, o, x, y, e + 5) for (k, o, x, y, e) in rows_ok]
for tag, rows in (("clean", rows_ok), ("perturbed+5d", rows_bad)):
    r = BS._run_bay((sub, rows, secs, 7, bay, os.path.abspath("solver")))
    print(json.dumps({"start": tag, "base": round(r[2]),
                      "got": round(r[3]),
                      "improved": r[1] is not None}))
