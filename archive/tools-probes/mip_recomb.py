"""Pattern-recombination CP-SAT (math-opt track, 07-30).
Mine every saved board of one problem for per-block verified placements
(bay, ori, x, y); CP-SAT picks one pattern per block + an entry day,
with bbox-overlapping same-bay pairs forced time-disjoint (conservative
=> checker-feasible by construction since every crane rule is
footprint-overlap based). Objective: w1*sum(W_i * tardy_i) + w3*Z3
(pattern-constant) [+ Z2 approx by bay-workload balance].
Usage: mip_recomb.py <pnum> <time_limit_s>
"""
import glob
import json
import math
import sys

from ortools.sat.python import cp_model

pnum = int(sys.argv[1])
TL = int(sys.argv[2]) if len(sys.argv) > 2 else 900
prob = json.load(open(f"data/preliminary_test/prob_{pnum}.json"))
n = len(prob["blocks"])
m = len(prob["bays"])
w = prob["weights"]
D = [int(round(b["due_date"])) for b in prob["blocks"]]
R = [int(math.ceil(b["release_time"] - 1e-9)) for b in prob["blocks"]]
P = [max(1, int(math.ceil(b["processing_time"] - 1e-9)))
     for b in prob["blocks"]]
W = [b["workload"] for b in prob["blocks"]]
pref = [b["bay_preferences"] for b in prob["blocks"]]
H = max(D) + max(P) + 40

def bbox(blk, oi):
    vs = [v for L in prob["blocks"][blk]["shape"][oi]["layers"] for v in L]
    xs = [v[0] for v in vs]; ys = [v[1] for v in vs]
    return min(xs), min(ys), max(xs), max(ys)

# ---- mine patterns
menus = [dict() for _ in range(n)]      # (bay,ori,x,y) -> count
for f in glob.glob(f"runs/*/prob_{pnum}.sol.json"):
    try:
        sol = json.load(open(f))
    except Exception:
        continue
    for t, ops in sol.get("operations", {}).items():
        for op in ops:
            if op["type"] == "ENTRY":
                k = (op["bay_id"], op["orient_idx"], op["x"], op["y"])
                d = menus[op["block_id"]]
                d[k] = d.get(k, 0) + 1
MENU = 10
pats = []
for i in range(n):
    top = sorted(menus[i].items(), key=lambda t: -t[1])[:MENU]
    pats.append([k for k, _ in top])
    if not pats[i]:
        print(json.dumps({"error": f"no patterns for block {i}"}))
        sys.exit(1)
print(json.dumps({"mined_boards": len(glob.glob(
    f'runs/*/prob_{pnum}.sol.json')),
    "menu_sizes": sorted(len(p) for p in pats)[:5]}), flush=True)

# absolute bboxes per pattern
pbb = []
for i in range(n):
    row = []
    for (b_, o_, x_, y_) in pats[i]:
        x0, y0, x1, y1 = bbox(i, o_)
        row.append((b_, x0 + x_, y0 + y_, x1 + x_, y1 + y_))
    pbb.append(row)

mdl = cp_model.CpModel()
lit = [[mdl.NewBoolVar(f"p{i}_{k}") for k in range(len(pats[i]))]
       for i in range(n)]
for i in range(n):
    mdl.AddExactlyOne(lit[i])
e = [mdl.NewIntVar(R[i], H - P[i], f"e{i}") for i in range(n)]
tardy = [mdl.NewIntVar(0, H, f"t{i}") for i in range(n)]
for i in range(n):
    mdl.AddMaxEquality(tardy[i], [e[i] + P[i] - D[i], 0])

def overlap(a, b):
    return a[1] < b[3] and b[1] < a[3] and a[2] < b[4] and b[2] < a[4]

ncon = 0
for i in range(n):
    for j in range(i + 1, n):
        for ki, bi in enumerate(pbb[i]):
            for kj, bj in enumerate(pbb[j]):
                if bi[0] != bj[0] or not overlap(bi, bj):
                    continue
                o = mdl.NewBoolVar("")
                mdl.Add(e[i] + P[i] <= e[j]).OnlyEnforceIf(
                    [lit[i][ki], lit[j][kj], o])
                mdl.Add(e[j] + P[j] <= e[i]).OnlyEnforceIf(
                    [lit[i][ki], lit[j][kj], o.Not()])
                ncon += 1
print(json.dumps({"disjunctions": ncon}), flush=True)

smax = [max(pref[i]) for i in range(n)]
# checker objectives (utils): obj1 = plain tardy days (NOT workload
# weighted); obj2 = max pair |u_a*load_a - u_b*load_b|, u_j =
# avg_bay_area/(W_j*H_j), load_j = sum of workloads in bay j.
areas = [by["width"] * by["height"] for by in prob["bays"]]
uj = [sum(areas) / m / a for a in areas]
SC = 1000
load = []
for j in range(m):
    lo = mdl.NewIntVar(0, int(sum(W) * max(uj) * SC) + 1, f"load{j}")
    mdl.Add(lo == sum(int(round(uj[j] * W[i] * SC)) * lit[i][k]
                      for i in range(n) for k in range(len(pats[i]))
                      if pats[i][k][0] == j))
    load.append(lo)
z2 = mdl.NewIntVar(0, int(sum(W) * max(uj) * SC) + 1, "z2")
for a in range(m):
    for b in range(m):
        if a != b:
            mdl.Add(z2 >= load[a] - load[b])
mdl.Minimize(
    sum(int(round(w["w1"])) * SC * tardy[i] for i in range(n)) +
    int(round(w["w2"])) * z2 +
    sum(int(round(w["w3"] * (smax[i] - pref[i][pats[i][k][0]]))) * SC
        * lit[i][k] for i in range(n) for k in range(len(pats[i]))))

# WARM START: hint the current best board (its patterns are in the
# menus by construction) so the solver starts at our incumbent and only
# needs to improve within the vocabulary.
import os
hint_f = os.environ.get("HINT", f"runs/ph1-s7/prob_{pnum}.sol.json")
try:
    hs = json.load(open(hint_f))
    hent, hmeta = {}, {}
    for t, ops in hs["operations"].items():
        for op in ops:
            if op["type"] == "ENTRY":
                hent[op["block_id"]] = int(t)
                hmeta[op["block_id"]] = (op["bay_id"], op["orient_idx"],
                                         op["x"], op["y"])
    nh = 0
    for i in range(n):
        if i in hmeta and hmeta[i] in pats[i]:
            k = pats[i].index(hmeta[i])
            mdl.AddHint(lit[i][k], 1)
            mdl.AddHint(e[i], hent[i])
            nh += 1
    print(json.dumps({"hinted": nh}), flush=True)
except Exception as ex:
    print(json.dumps({"hint_fail": str(ex)}), flush=True)

sv = cp_model.CpSolver()
sv.parameters.max_time_in_seconds = TL
sv.parameters.num_search_workers = 16
st = sv.Solve(mdl)
print(json.dumps({"status": sv.StatusName(st),
                  "obj": sv.ObjectiveValue()
                  if st in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None,
                  "bound": sv.BestObjectiveBound()}), flush=True)
if st in (cp_model.OPTIMAL, cp_model.FEASIBLE):
    out = {}
    for i in range(n):
        k = next(k for k in range(len(pats[i]))
                 if sv.Value(lit[i][k]))
        b_, o_, x_, y_ = pats[i][k]
        ei = sv.Value(e[i])
        out[i] = [b_, x_, y_, o_, ei, ei + P[i]]
    json.dump(out, open(f"runs/mip_recomb_p{pnum}.json", "w"))
    print("plan saved", flush=True)
