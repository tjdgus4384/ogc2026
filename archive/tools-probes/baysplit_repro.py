"""Offline test of per-bay parallel deep re-optimization.
Usage: baysplit_repro.py <pnum> <seconds>"""
import json, math, os, sys, time
pnum = int(sys.argv[1]); secs = float(sys.argv[2])
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, os.environ.get("REFDIR", "solver"))
import myalgorithm as M
import baysplit as BS
import utils
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
r0 = utils.check_feasibility(prob, brd)
print(json.dumps({"start": r0["objective"], "obj1": r0["obj1"]}))
out = BS.optimize(prob, flat, time.time()+secs, seed=7,
                  diag=lambda t, **k: print(json.dumps({"diag":t, **k})),
                  solver_dir=os.path.abspath(os.environ.get("REFDIR","solver")))
if out is None:
    print("no improvement"); raise SystemExit
recs = {i: [out[i*7], out[i*7+2], out[i*7+3], out[i*7+1],
            out[i*7+4], out[i*7+5]] for i in range(n) if out[i*7+6]}
sol = M._recs_to_sol(recs)
r = utils.check_feasibility(prob, sol)
print(json.dumps({"feasible": r["feasible"], "objective": r.get("objective"),
                  "obj1": r.get("obj1"), "viol": len(r.get("violations", []))}))
if r["feasible"] and r["objective"] < r0["objective"]:
    json.dump(sol, open(f"runs/mipboards/prob_{pnum}.sol.json","w"))
    print(json.dumps({"fed": r["objective"]}))
