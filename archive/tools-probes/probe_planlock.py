"""CP-SAT Z1-plan assignment as PARTIAL anneal-long lock + mid-anneal
unlock (the toolkit that did not exist when plan-locks were refuted).
P4: lock the J blocks with largest plan-vs-free-entry delay to the
plan's bay; unlockF=0.6 active by default. J sweep vs control."""
import json
import math
import sys
import time

sys.path.insert(0, "solver")
import myalgorithm as M

prob = json.load(open("data/preliminary_test/prob_4.json"))
plan = json.load(open("runs/plan_p4.json"))
n = len(prob["blocks"])
D = [int(round(b["due_date"])) for b in prob["blocks"]]
order = sorted(range(n), key=lambda i: D[i])
rate = M._rate(n, len(prob["bays"]))
# rank blocks by how much EARLIER the plan schedules them vs our best
sol = json.load(open("runs/plock6-s7/prob_4.sol.json"))
ent = {}
for t, ops in sol["operations"].items():
    for op in ops:
        if op["type"] == "ENTRY":
            ent[op["block_id"]] = int(t)
gain = sorted(range(n),
              key=lambda i: -(ent.get(i, 0) - plan[i]["entry"]))
SEC = 110.0
for J in [0, 30, 60, 150]:
    asg = [-1] * n
    for i in gain[:J]:
        asg[i] = plan[i]["bay"]
    for seed in [7, 8]:
        eng, _, _ = M._fresh(prob)
        eng.set_assignment_lock(asg if J else [])
        rc = eng.reconstruct_ordered(asg, order, False, 7)
        if rc != -1:
            eng.set_assignment_lock([])
            print(json.dumps({"J": J, "seed": seed, "cfail": rc}),
                  flush=True)
            continue
        eng.run_portfolio(M._THR, SEC + 10, 1, 0.05, 0.15, 0.002, 0.9995,
                          seed, 1, [eng.get_solution()],
                          int(0.9 * SEC * rate))
        eng.set_assignment_lock([])
        M._pref_polish(prob, eng, time.time() + 15.0)
        print(json.dumps({"J": J, "seed": seed,
                          "obj": eng.objective()}), flush=True)
