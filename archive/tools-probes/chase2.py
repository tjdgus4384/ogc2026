"""Schedule-first construction: chase the certified Z1=0 timetable
chronologically, deferring only what physically fails to pack.

The area relaxation certifies a timetable with zero tardiness
(runs/plan_p*.json). The old plan-chase HARD-locked its bay assignment
and died at the first unpackable block; measurement nevertheless showed
77-85% of the plan's placements are individually realizable. This keeps
the plan's TIMES as soft targets: sweep days chronologically, place each
block due to enter today (its own plan bay first, any bay second), and
on failure defer the block one day -- tardiness enters only where the
packing genuinely forces it.

Usage: chase2.py <pnum> [anneal_secs]
"""
import json
import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "solver"))
os.chdir(ROOT)

import sscore

pnum = int(sys.argv[1])
ANN = float(sys.argv[2]) if len(sys.argv) > 2 else 300.0

prob = json.load(open(f"data/preliminary_test/prob_{pnum}.json"))
plan = json.load(open(f"runs/plan_p{pnum}.json"))
B = prob["blocks"]; bays = prob["bays"]; W = prob["weights"]
n = len(B)
P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
R = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]
D = [int(round(b["due_date"])) for b in B]
PREF = [[float(v) for v in b["bay_preferences"]] for b in B]
HOR = (max(D) + max(P)) * 2 + 200

eng = sscore.Engine()
eng.init_problem(n, len(bays), R, D, P,
                 [float(b["workload"]) for b in B], PREF,
                 [float(b["width"]) for b in bays],
                 [float(b["height"]) for b in bays],
                 float(W["w1"]), float(W["w2"]), float(W["w3"]))
for i in range(n):
    for oi in range(len(B[i]["shape"])):
        L = [[list(v) for v in lay] for lay in B[i]["shape"][oi]["layers"]]
        rx, ry = L[0][0]
        eng.add_orient(i, [[(x - rx, y - ry) for (x, y) in lay] for lay in L])
eng.build_occ(HOR)

target = [max(R[i], int(plan[i]["entry"])) for i in range(n)]
pbay = [int(plan[i]["bay"]) for i in range(n)]
cur = [0] * (n * 7)
queue = {}
for i in range(n):
    queue.setdefault(target[i], []).append(i)

t0 = time.time()
day = 0
deferred = placed = 0
while day < HOR - 1 and placed < n:
    def hard(i):
        vs = [v for lay in B[i]["shape"][0]["layers"] for v in lay]
        w = max(v[0] for v in vs) - min(v[0] for v in vs)
        h = max(v[1] for v in vs) - min(v[1] for v in vs)
        return -(w * h * P[i])          # hardest (biggest) first
    todo = sorted(queue.pop(day, []), key=hard)
    for i in todo:
        ok = False
        # exact-day placement: plan bay first, then any bay
        for wantbay in ([pbay[i], -1] if pbay[i] >= 0 else [-1]):
            raw = eng.feasible_positions(i, 12, day)
            for q in range(0, len(raw), 5):
                j, oi, x, y, e = raw[q:q+5]
                if e != day:
                    continue
                if wantbay >= 0 and j != wantbay:
                    continue
                if eng.place_at(i, j, oi, x, y, e):
                    cur[i*7:i*7+7] = [j, oi, x, y, e, e + P[i], 1]
                    placed += 1; ok = True
                    break
            if ok:
                break
        if not ok:
            queue.setdefault(day + 1, []).append(i)
            deferred += 1
    day += 1

bad = [i for i in range(n) if cur[i*7+6] and not eng.vp_ok(i)]
z1, z2, z3, tot = eng.objective()
print(json.dumps({"p": pnum, "placed": placed, "n": n,
                  "deferrals": deferred, "z1": z1, "z2": z2, "z3": z3,
                  "obj": tot, "vp_violations": len(bad),
                  "construct_secs": round(time.time() - t0, 1)}), flush=True)
if ANN > 0 and placed == n and not bad:
    eng.run_portfolio(4, ANN, 1, 0.05, 0.15, 0.002, 0.9997, 7, 1, [cur], 0)
    z1b, _, _, tot2 = eng.objective()
    print(json.dumps({"after_anneal": tot2, "z1": z1b}), flush=True)
