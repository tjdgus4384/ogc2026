"""Branch-and-check (logic-based Benders) over the ASSIGNMENT.

The measurements left one suspect: which blocks share a bay. Every
method that froze an assignment and rearranged geometry returned 0.0%,
and locking different whole assignments changes the reachable objective
by ~15% on P4 -- but an assignment cannot be judged on paper, because
its cost depends on a packing that only the engine can evaluate.

So decompose along exactly that seam:

    master      choose y[i][j] with Z2 and Z3 in closed form and Z1
                represented by per-bay variables T_j bounded from below
                by cuts learned so far.  No geometry at all -- it stays
                small and fast.
    subproblem  give the chosen block set of each bay to the PRODUCTION
                ENGINE (exact polygons, exact crane rules, exact time
                axis) and let it pack; read the achieved tardiness.
    cut         T_j >= T*(S_j) - BIG * sum_{i in S_j} (1 - y[i][j])
                -- valid because dropping any block from the set can
                only lower the achievable tardiness, and binding only
                while the whole set stays together in that bay.

The cuts are reusable structural knowledge: each one says "this group,
in this bay, cannot beat T*". That is the part a heuristic can later be
distilled from.

  bcheck.py <prob.json> <out.json> <total_seconds> <sub_seconds> [board]
"""
import json
import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "solver"))
os.chdir(ROOT)
import myalgorithm as M  # noqa: E402
from ortools.sat.python import cp_model as cp  # noqa: E402

prob_path, out_path = sys.argv[1], sys.argv[2]
TOTAL = float(sys.argv[3]) if len(sys.argv) > 3 else 900.0
SUB = float(sys.argv[4]) if len(sys.argv) > 4 else 60.0
BOARD = sys.argv[5] if len(sys.argv) > 5 else None

p = json.load(open(prob_path))
B, bays, W = p["blocks"], p["bays"], p["weights"]
n, m = len(B), len(bays)
D = [int(round(b["due_date"])) for b in B]
P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
L = [float(b["workload"]) for b in B]
PREF = [[float(v) for v in b["bay_preferences"]] for b in B]
SMAX = [max(pr) for pr in PREF]
w1, w2, w3 = float(W["w1"]), float(W["w2"]), float(W["w3"])
ar = [b["width"] * b["height"] for b in bays]
SC = 100
uu = [int(round(SC * (sum(ar) / m) / ar[j])) for j in range(m)]
HOR = max(D) + max(P) + 40

mdl = cp.CpModel()
y = [[mdl.NewBoolVar(f"y{i}_{j}") for j in range(m)] for i in range(n)]
for i in range(n):
    mdl.AddExactlyOne(y[i])
T = [mdl.NewIntVar(0, n * HOR, f"T{j}") for j in range(m)]
zmax = mdl.NewIntVar(0, 10 ** 9, "zmax")
zmin = mdl.NewIntVar(0, 10 ** 9, "zmin")
for j in range(m):
    load = sum(int(round(L[i])) * y[i][j] for i in range(n))
    mdl.Add(zmax >= uu[j] * load)
    mdl.Add(zmin <= uu[j] * load)
reg = sum(int(round(SMAX[i] - PREF[i][j])) * y[i][j]
          for i in range(n) for j in range(m))
mdl.Minimize(SC * int(w1) * sum(T) + SC * int(w3) * reg
             + int(w2) * (zmax - zmin))

# warm start from an incumbent board, when given
if BOARD:
    for t, day in json.load(open(BOARD))["operations"].items():
        for o in day:
            if o["type"] == "ENTRY":
                for j in range(m):
                    mdl.AddHint(y[o["block_id"]][j], 1 if j == o["bay_id"] else 0)

# ---- ENERGETIC VALID INEQUALITIES (a real Z1 model for the master)
# The first version gave the master no notion of tardiness at all: its
# bound sat at 116k against an actual 3.7M, so it wandered into
# assignments whose geometry was hopeless (P6: 73 of 73 proposals could
# not even be seeded). Area-time is the currency that fixes this. A bay
# processes at most area_j of area-time per day, so for any threshold t
# the work that is DUE by t and assigned to j needs
#     sum_{D_i <= t} area_i * P_i * y[i][j]
# of area-time inside a window of capacity area_j * t. Whatever exceeds
# that must finish later, and the excess divided by area_j is a lower
# bound on how many days late it lands -- hence on sum-tardiness.
# Unlike the bbox-area cut that was refuted for the real problem, this
# uses each block's own polygon area, which no interlocking can shrink.
AREA = []
for i in range(n):
    sh = B[i]["shape"][0]["layers"]
    xs = [v[0] for lay in sh for v in lay]
    ys = [v[1] for lay in sh for v in lay]
    AREA.append(max(1.0, (max(xs) - min(xs)) * (max(ys) - min(ys))))
thr = sorted({D[i] for i in range(n)})
if len(thr) > 24:
    step = len(thr) / 24.0
    thr = [thr[min(len(thr) - 1, int(k * step))] for k in range(24)]
energetic = 0
for j in range(m):
    for t in thr:
        cap = ar[j] * t
        dem = [(i, AREA[i] * P[i]) for i in range(n) if D[i] <= t]
        if not dem or sum(d for _, d in dem) <= cap:
            continue
        # T_j * area_j >= (demand due by t) - capacity by t
        mdl.Add(int(ar[j]) * T[j] >= sum(int(d) * y[i][j] for i, d in dem)
                - int(cap))
        energetic += 1

BIG = HOR
t0 = time.time()
rounds, best = [], None
best_sol = None
while time.time() - t0 < TOTAL - SUB - 10:
    sv = cp.CpSolver()
    sv.parameters.max_time_in_seconds = min(60.0, TOTAL - (time.time() - t0))
    sv.parameters.num_search_workers = 4
    st = sv.Solve(mdl)
    if st not in (cp.OPTIMAL, cp.FEASIBLE):
        rounds.append({"master": sv.StatusName(st)})
        break
    asg = [next(j for j in range(m) if sv.Value(y[i][j])) for i in range(n)]
    master_lb = sv.ObjectiveValue() / SC

    # subproblem: the engine packs this assignment with exact geometry
    eng, seeds = M._seed_free(p, asg=asg)
    if eng is None:
        # Locate WHICH bay is impossible and forbid that set, not just
        # this one assignment. Forbidding the whole proposal taught the
        # master nothing: P6 burned 73 rounds re-proposing variations of
        # the same impossible bay.
        bad = 0
        for j in range(m):
            S = [i for i in range(n) if asg[i] == j]
            if not S:
                continue
            sub = {"bays": [bays[j]],
                   "blocks": [{**B[i], "bay_preferences": [PREF[i][j]]}
                              for i in S],
                   "weights": W}
            e2, _ = M._seed_free(sub)
            if e2 is None:
                mdl.Add(sum(y[i][j] for i in S) <= len(S) - 1)
                bad += 1
        if bad == 0:
            mdl.AddBoolOr([y[i][asg[i]].Not() for i in range(n)])
        rounds.append({"master_lb": round(master_lb), "sub": "seed-failed",
                       "subset_cuts": bad})
        continue
    eng.run_portfolio(4, SUB, 1, 0.05, 0.15, 0.002, 0.9995, 7, len(seeds), seeds)
    flat = eng.get_solution()
    z = eng.objective()
    if best is None or z[3] < best:
        best, best_sol = z[3], list(flat)

    # per-bay achieved tardiness -> cuts
    added = 0
    for j in range(m):
        S = [i for i in range(n) if asg[i] == j]
        tj = sum(max(0, flat[i*7+5] - D[i]) for i in S if flat[i*7+6])
        if tj <= 0:
            continue
        mdl.Add(T[j] >= int(tj) - BIG * sum(1 - y[i][j] for i in S))
        added += 1
    rounds.append({"master_lb": round(master_lb), "engine_obj": z[3],
                   "z1": z[0], "z2": z[1], "z3": z[2], "cuts": added,
                   "elapsed": round(time.time() - t0, 1)})
    if added == 0:
        break

res = {"board": os.path.basename(prob_path), "n": n, "m": m,
       "energetic_rows": energetic, "rounds": rounds, "best_engine_obj": best,
       "wall": round(time.time() - t0, 1)}
if best_sol is not None:
    eng = M._build_engine(p)
    eng.build_occ(HOR); eng.set_solution(best_sol); eng.build_occ(HOR)
    sol = M._recs_to_sol(M._extract(eng, n))
    chk = M._check(p, sol)
    res["checker_feasible"] = None if chk is None else bool(chk.get("feasible"))
    res["checker_obj"] = None if chk is None else chk.get("objective")
    if chk is not None and chk.get("feasible") and out_path != "/dev/null":
        json.dump(sol, open(out_path, "w"))
print(json.dumps(res))
