"""Recombination CP-SAT v3: kernel-oracle compatibility semantics.
Coexistence of a same-bay overlapping pattern pair is allowed exactly in
the temporal orders the kernel certified; otherwise time-disjoint.
Incumbent fully hinted. Usage: mip_v3.py <pnum> <TL>"""
import json
import math
import sys

from ortools.sat.python import cp_model

pnum = int(sys.argv[1])
TL = int(sys.argv[2]) if len(sys.argv) > 2 else 420
prob = json.load(open(f"data/preliminary_test/prob_{pnum}.json"))
n = len(prob["blocks"]); m = len(prob["bays"]); w = prob["weights"]
D = [int(round(b["due_date"])) for b in prob["blocks"]]
R = [int(math.ceil(b["release_time"] - 1e-9)) for b in prob["blocks"]]
P = [max(1, int(math.ceil(b["processing_time"] - 1e-9)))
     for b in prob["blocks"]]
W = [b["workload"] for b in prob["blocks"]]
pref = [b["bay_preferences"] for b in prob["blocks"]]
H = max(D) + max(P) + 40
# hint may live on the engine's hmul-extended horizon (congested boards)
import os as _os
_hp0 = _os.environ.get("HINT", f"runs/mipboards/prob_{pnum}.sol.json")
if not _os.path.exists(_hp0):
    _hp0 = f"runs/ph1-s7/prob_{pnum}.sol.json"
_hs0 = json.load(open(_hp0))
for _t, _ops in _hs0["operations"].items():
    for _op in _ops:
        if _op["type"] == "EXIT":
            H = max(H, int(_t) + 1)
pats = [[tuple(k) for k in row]
        for row in json.load(open(f"runs/pats_p{pnum}.json"))]
compat = json.load(open(f"runs/compat_p{pnum}.json"))

mdl = cp_model.CpModel()
lit = [[mdl.NewBoolVar(f"p{i}_{k}") for k in range(len(pats[i]))]
       for i in range(n)]
for i in range(n):
    mdl.AddExactlyOne(lit[i])
e = [mdl.NewIntVar(R[i], H - P[i], f"e{i}") for i in range(n)]
td = [mdl.NewIntVar(0, H, f"t{i}") for i in range(n)]
for i in range(n):
    mdl.AddMaxEquality(td[i], [e[i] + P[i] - D[i], 0])

import os
hint_path = os.environ.get(
    "HINT", f"runs/mipboards/prob_{pnum}.sol.json")
if not os.path.exists(hint_path):
    hint_path = f"runs/ph1-s7/prob_{pnum}.sol.json"
best = json.load(open(hint_path))
hent, hmeta = {}, {}
for t, ops in best["operations"].items():
    for op in ops:
        if op["type"] == "ENTRY":
            hent[op["block_id"]] = int(t)
            hmeta[op["block_id"]] = (op["bay_id"], op["orient_idx"],
                                     op["x"], op["y"])
nh = 0
for i in range(n):
    if i in hmeta and hmeta[i] in pats[i]:
        mdl.AddHint(lit[i][pats[i].index(hmeta[i])], 1)
        mdl.AddHint(e[i], hent[i])
        nh += 1
print(json.dumps({"hinted": nh}), flush=True)

# compat[key] = 9 verdicts over (sign(e_i-e_j), sign(f_i-f_j)) in
# row-major (-1,0,1)x(-1,0,1); None = unrealizable. Forbid each illegal
# combo while both patterns chosen and the pair coexists.
nc = 0
for key, v in compat.items():
    i, ki, j, kj = map(int, key.split(","))
    if all(x is True for x in v):
        continue
    li, lj = lit[i][ki], lit[j][kj]
    dj1 = mdl.NewBoolVar("")
    mdl.Add(e[i] + P[i] <= e[j]).OnlyEnforceIf(dj1)
    mdl.Add(e[i] + P[i] > e[j]).OnlyEnforceIf(dj1.Not())
    dj2 = mdl.NewBoolVar("")
    mdl.Add(e[j] + P[j] <= e[i]).OnlyEnforceIf(dj2)
    mdl.Add(e[j] + P[j] > e[i]).OnlyEnforceIf(dj2.Not())
    elt = mdl.NewBoolVar(""); eeq = mdl.NewBoolVar("")
    egt = mdl.NewBoolVar("")
    mdl.AddExactlyOne([elt, eeq, egt])
    mdl.Add(e[i] < e[j]).OnlyEnforceIf(elt)
    mdl.Add(e[i] == e[j]).OnlyEnforceIf(eeq)
    mdl.Add(e[i] > e[j]).OnlyEnforceIf(egt)
    flt = mdl.NewBoolVar(""); feq = mdl.NewBoolVar("")
    fgt = mdl.NewBoolVar("")
    mdl.AddExactlyOne([flt, feq, fgt])
    mdl.Add(e[i] + P[i] < e[j] + P[j]).OnlyEnforceIf(flt)
    mdl.Add(e[i] + P[i] == e[j] + P[j]).OnlyEnforceIf(feq)
    mdl.Add(e[i] + P[i] > e[j] + P[j]).OnlyEnforceIf(fgt)
    EL = [elt, eeq, egt]
    FL = [flt, feq, fgt]
    if i in hent and j in hent:          # hint aux literals (completion)
        hei, hej = hent[i], hent[j]
        hfi, hfj = hei + P[i], hej + P[j]
        mdl.AddHint(dj1, int(hfi <= hej))
        mdl.AddHint(dj2, int(hfj <= hei))
        mdl.AddHint(elt, int(hei < hej))
        mdl.AddHint(eeq, int(hei == hej))
        mdl.AddHint(egt, int(hei > hej))
        mdl.AddHint(flt, int(hfi < hfj))
        mdl.AddHint(feq, int(hfi == hfj))
        mdl.AddHint(fgt, int(hfi > hfj))
    for a_ in range(3):
        for b_ in range(3):
            if v[a_ * 3 + b_] is not True:
                mdl.AddBoolOr([li.Not(), lj.Not(), dj1, dj2,
                               EL[a_].Not(), FL[b_].Not()])
    nc += 1
print(json.dumps({"constrained_pairs": nc, "total_pairs": len(compat)}),
      flush=True)

areas = [by["width"] * by["height"] for by in prob["bays"]]
uj = [sum(areas) / m / a for a in areas]
SC = 1000
load = []
for j in range(m):
    lo = mdl.NewIntVar(0, int(sum(W) * max(uj) * SC) + 1, f"l{j}")
    mdl.Add(lo == sum(int(round(uj[j] * W[i] * SC)) * lit[i][k]
                      for i in range(n) for k in range(len(pats[i]))
                      if pats[i][k][0] == j))
    load.append(lo)
z2 = mdl.NewIntVar(0, int(sum(W) * max(uj) * SC) + 1, "z2")
for a in range(m):
    for b in range(m):
        if a != b:
            mdl.Add(z2 >= load[a] - load[b])
smax = [max(pref[i]) for i in range(n)]
mdl.Minimize(
    sum(int(round(w["w1"])) * SC * td[i] for i in range(n)) +
    int(round(w["w2"])) * z2 +
    sum(int(round(w["w3"] * (smax[i] - pref[i][pats[i][k][0]]))) * SC
        * lit[i][k] for i in range(n) for k in range(len(pats[i]))))



# FIX-AND-OPTIMIZE (env FREEK): freeze all but K blocks to their hint
# placement+entry (constants; presolve prunes their constraints). Free
# set = top tardy blocks + neighbors by same-bay coexistence in hint.
FREEK = int(os.environ.get("FREEK", "0"))
if os.environ.get("FREESEL") == "list" and nh == n:
    # explicit free set (comma-separated block ids): the targeted
    # tardy-unblock mode -- everything else frozen to hint.
    free = {int(x) for x in os.environ["FREELIST"].split(",")}
    for i in range(n):
        if i in free:
            continue
        mdl.Add(lit[i][pats[i].index(hmeta[i])] == 1)
        mdl.Add(e[i] == hent[i])
    print(json.dumps({"free_list": len(free)}), flush=True)
elif os.environ.get("FREESEL") == "window" and nh == n:
    # rolling time-window LNS: free every block whose hint entry lies in
    # [W0, W0+WLEN) -- whole time-slices restructure at once (stronger
    # than tardiness-ranked FREEK for global-structure gaps).
    W0 = int(os.environ.get("W0", "0"))
    WLEN = int(os.environ.get("WLEN", "20"))
    free = {i for i in range(n) if W0 <= hent[i] < W0 + WLEN}
    for i in range(n):
        if i in free:
            continue
        mdl.Add(lit[i][pats[i].index(hmeta[i])] == 1)
        mdl.Add(e[i] == hent[i])
    print(json.dumps({"free_window": len(free), "W0": W0, "WLEN": WLEN}),
          flush=True)
elif os.environ.get("FREESEL") == "regret" and nh == n:
    free = set()
    for i in range(n):
        tb = max(range(len(pref[i])), key=lambda j: pref[i][j])
        if pref[i][hmeta[i][0]] < pref[i][tb]:
            free.add(i)
    for i in range(n):
        if i in free:
            continue
        mdl.Add(lit[i][pats[i].index(hmeta[i])] == 1)
        mdl.Add(e[i] == hent[i])
    print(json.dumps({"free_regret": len(free)}), flush=True)
elif FREEK > 0 and nh == n:
    tardy_order = sorted(
        (i for i in range(n)),
        key=lambda i: -(max(0, hent[i] + P[i] - D[i]) * 1000 + (i * int(os.environ.get("ROT", "7"))) % 97))
    free = set(tardy_order[:FREEK])
    for i in range(n):
        if i in free:
            continue
        mdl.Add(lit[i][pats[i].index(hmeta[i])] == 1)
        mdl.Add(e[i] == hent[i])
    print(json.dumps({"free": len(free)}), flush=True)

class Prog(cp_model.CpSolverSolutionCallback):
    def __init__(self):
        super().__init__()
    def on_solution_callback(self):
        print(json.dumps({"t": round(self.WallTime(), 1),
                          "obj": self.ObjectiveValue() / SC}), flush=True)

sv = cp_model.CpSolver()
if os.environ.get("DIAGHINT"):
    sv.parameters.fix_variables_to_their_hinted_value = True
    sv.parameters.log_search_progress = True
sv.parameters.max_time_in_seconds = TL
sv.parameters.num_search_workers = 16
st = sv.Solve(mdl, Prog())
print(json.dumps({"status": sv.StatusName(st),
                  "obj_true": sv.ObjectiveValue() / SC
                  if st in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None,
                  "bound_true": sv.BestObjectiveBound() / SC}), flush=True)
if st in (cp_model.OPTIMAL, cp_model.FEASIBLE):
    out = {}
    for i in range(n):
        k = next(k for k in range(len(pats[i])) if sv.Value(lit[i][k]))
        b_, o_, x_, y_ = pats[i][k]
        ei = sv.Value(e[i])
        out[i] = [b_, x_, y_, o_, ei, ei + P[i]]
    json.dump(out, open(f"runs/mip_v3_p{pnum}.json", "w"))
    print("plan saved", flush=True)
