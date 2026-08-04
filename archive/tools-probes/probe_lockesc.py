"""Lock-escalation value probe (P3, equal total budget):
(a) single run, lock top-25% spread;
(b) two phases, lock25 then lock40, phase 2 continues from phase-1 board.
"""
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

def asg_top(k):
    a = [-1] * n
    for i in spread[:k]:
        a[i] = max(range(len(pref[i])), key=lambda j: pref[i][j])
    return a

def run(eng, asg, sec, seed):
    eng.set_assignment_lock(asg)
    core = int(0.9 * sec * rate)
    eng.run_portfolio(M._THR, sec + 5.0, 1, 0.05, 0.15, 0.002, 0.9995,
                      seed, 1, [eng.get_solution()], core)
    eng.set_assignment_lock([])

# (a) control: single 110s lock25
eng, _, _ = M._fresh(prob)
a25 = asg_top(n // 4)
assert eng.reconstruct_ordered(a25, order, False, 7) == -1
run(eng, a25, 110.0, 7)
M._pref_polish(prob, eng, time.time() + 30.0)
print(json.dumps({"mode": "single110_lock25", "obj": eng.objective()}),
      flush=True)

# (b) escalation: 55s lock25 -> 55s lock40 (continue)
eng, _, _ = M._fresh(prob)
assert eng.reconstruct_ordered(a25, order, False, 7) == -1
run(eng, a25, 55.0, 7)
mid = eng.objective()[3]
run(eng, asg_top(int(n * 0.4)), 55.0, 8)
M._pref_polish(prob, eng, time.time() + 30.0)
print(json.dumps({"mode": "esc25to40", "mid": mid,
                  "obj": eng.objective()}), flush=True)
