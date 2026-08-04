"""Convert a mip_recomb plan to operations JSON and score with the
OFFICIAL checker. Usage: mip_score.py <pnum>"""
import json
import sys

sys.path.insert(0, "solver")
import myalgorithm as M
import utils

pnum = int(sys.argv[1])
prob = json.load(open(f"data/preliminary_test/prob_{pnum}.json"))
plan = json.load(open(f"runs/mip_recomb_p{pnum}.json"))
recs = {int(i): v for i, v in plan.items()}
sol = M._recs_to_sol(recs)
res = utils.check_feasibility(prob, sol)
print(json.dumps({"feasible": res.get("feasible"),
                  "objective": res.get("objective"),
                  "obj1": res.get("obj1"), "obj2": res.get("obj2"),
                  "obj3": res.get("obj3"),
                  "violations": res.get("violations", [])[:3]}))
if res.get("feasible"):
    json.dump(sol, open(f"runs/mip_recomb_p{pnum}.sol.json", "w"))
