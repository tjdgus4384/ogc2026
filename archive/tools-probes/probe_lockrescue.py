"""Safety-valve schedule probe: locked-40%% run at 60%% core; if Z1>0
(cliff draw) rescue with an unlocked 40%%-core run from scratch; else
keep. Test on seed 7 (the cliff draw) and seed 8 (the good draw)."""
import json
import math
import sys
import time

sys.path.insert(0, "solver")
import myalgorithm as M

prob = json.load(open("data/preliminary_test/prob_3.json"))
n = len(prob["blocks"])
pref = [b["bay_preferences"] for b in prob["blocks"]]
D = [int(round(b["due_date"])) for b in prob["blocks"]]
spread = sorted(range(n), key=lambda i: -(max(pref[i]) - min(pref[i])))
rate = M._rate(n, len(prob["bays"]))
order = sorted(range(n), key=lambda i: D[i])
asg = [-1] * n
for i in spread[:int(n * 0.4)]:
    asg[i] = max(range(len(pref[i])), key=lambda j: pref[i][j])
SEC = 223.0
for seed in [7, 8]:
    eng, _, _ = M._fresh(prob)
    assert eng.reconstruct_ordered(asg, order, False, 7) == -1
    eng.set_assignment_lock(asg)
    eng.run_portfolio(M._THR, SEC * 0.62, 1, 0.05, 0.15, 0.002, 0.9995,
                      seed, 1, [eng.get_solution()],
                      int(0.9 * SEC * 0.6 * rate))
    eng.set_assignment_lock([])
    o1 = eng.objective()
    if o1[0] > 0:                          # cliff draw -> unlocked rescue
        eng2, _, _ = M._fresh(prob)
        assert eng2.reconstruct_ordered([-1] * n, order, False, 7) == -1
        eng2.run_portfolio(M._THR, SEC * 0.4, 1, 0.05, 0.15, 0.002,
                          0.9995, seed, 1, [eng2.get_solution()],
                          int(0.9 * SEC * 0.4 * rate))
        M._pref_polish(prob, eng2, time.time() + 20.0)
        print(json.dumps({"seed": seed, "locked": o1,
                          "rescued": eng2.objective()}), flush=True)
    else:
        M._pref_polish(prob, eng, time.time() + 20.0)
        print(json.dumps({"seed": seed, "locked_kept": eng.objective()}),
              flush=True)
