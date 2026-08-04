"""Global placement MIP with lazy conflict cuts (Gurobi).

Every local class is measured dead here (single/2-exchange, exact
re-place at K<=120, whole-bay re-solve, bay-plan recombination proved
optimal at 0%). What none of them can do is decide ALL placements at
once. This does exactly that:

    choose one placement per block from a rich candidate vocabulary,
    subject to pairwise geometric/crane conflicts, minimising the true
    objective  w1*Z1 + w2*Z2 + w3*Z3.

    min  sum_{i,c} (w1*tard_{i,c} + w3*regret_{i,c}) z_{i,c}
         + w2*(zmax - zmin)
    s.t. sum_c z_{i,c} = 1                          one placement each
         z_{i,c} + z_{k,d} <= 1                     for conflicting pairs
         zmax >= sum u_j L_i z_{i,c} over bay j,  zmin <= ...

The conflict set is enormous, so it is separated LAZILY: start from the
incumbent as a warm start, solve, ask the engine's (now sound) pair
oracle which chosen pairs actually clash, add just those, repeat. Any
integer solution that survives separation is feasible by construction
and is then re-checked with the official checker.

Vocabulary = every position this block has held in ANY harvested board
(our runs disagree a lot, which is exactly the diversity a global model
needs) + fresh feasible positions probed against the incumbent.

Usage: globalmip.py <prob.json> <out.json> <sol.json> [sol.json ...]
Env:   GM_TL (seconds per MIP solve, default 120), GM_ROUNDS (default 6),
       GM_EXTRA (fresh candidates per block, default 12)
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
SOLVER = os.environ.get("GM_SOLVER", "gurobi")
if SOLVER == "cpsat":
    # CP-SAT backend: the Gurobi WLS academic licence caps concurrent
    # sessions (two runs died on an overage), and ortools has no such
    # limit. Same model, same separation loop -- CP-SAT has no lazy
    # callback, so it uses the outer round loop, which is exactly what
    # the round-based path already does.
    from ortools.sat.python import cp_model as _cp
else:
    import gurobipy as gp  # noqa: E402
    from gurobipy import GRB  # noqa: E402

prob_path, out_path = sys.argv[1], sys.argv[2]
sources = sys.argv[3:]
TL = float(os.environ.get("GM_TL", "120"))
ROUNDS = int(os.environ.get("GM_ROUNDS", "6"))
EXTRA = int(os.environ.get("GM_EXTRA", "12"))

p = json.load(open(prob_path))
B, bays, W = p["blocks"], p["bays"], p["weights"]
n, m = len(B), len(bays)
D = [int(round(b["due_date"])) for b in B]
P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
PREF = [[float(v) for v in b["bay_preferences"]] for b in B]
SMAX = [max(pr) for pr in PREF]
L = [float(b["workload"]) for b in B]
ar = [b["width"] * b["height"] for b in bays]
u = [(sum(ar) / m) / a for a in ar]
w1, w2, w3 = float(W["w1"]), float(W["w2"]), float(W["w3"])


def load(path):
    ops = json.load(open(path))["operations"]
    flat = [0] * (n * 7)
    for t, day in ops.items():
        for o in day:
            i = o["block_id"]
            if o["type"] == "ENTRY":
                flat[i*7:i*7+5] = [o["bay_id"], o["orient_idx"], o["x"],
                                   o["y"], int(t)]
                flat[i*7+6] = 1
            else:
                flat[i*7+5] = int(t)
    return flat


boards = [load(s) for s in sources]
eng = M._build_engine(p)
hor = max(D) + max(P) + 40
eng.build_occ(hor)
best_flat, best_obj = None, None
for fb in boards:
    eng.set_solution(fb); eng.build_occ(hor)
    v = eng.objective()[3]
    if best_obj is None or v < best_obj:
        best_obj, best_flat = v, list(fb)

# ---- vocabulary: every position any board gave this block, plus fresh probes
CAND = {i: [] for i in range(n)}
for fb in boards:
    for i in range(n):
        if fb[i*7+6]:
            CAND[i].append((fb[i*7], fb[i*7+1], fb[i*7+2], fb[i*7+3], fb[i*7+4]))
if EXTRA > 0:
    for i in range(n):
        part = list(best_flat); part[i*7+6] = 0
        eng.set_solution(part); eng.build_occ(hor)
        raw = eng.feasible_positions(i, EXTRA, 0)
        CAND[i] += [tuple(raw[q:q+5]) for q in range(0, len(raw), 5)]
GRID = int(os.environ.get("GM_GRID", "0"))
if GRID > 0:
    # DENSE VOCABULARY. The lazy separation means we never have to
    # materialise the quadratic conflict set, so the vocabulary can be
    # generated instead of harvested: for a few entry days (punctual
    # first) and every orientation, walk a stride-GRID lattice of the
    # bay and keep the positions that are individually containable.
    # Pairwise legality is the model's job, not the generator's.
    for i in range(n):
        pun = max(int(math.ceil(B[i]["release_time"] - 1e-9)), D[i] - P[i])
        ents = sorted({pun, max(0, pun - 1), pun + 1,
                       int(math.ceil(B[i]["release_time"] - 1e-9))})
        for j in range(m):
            Wb, Hb = bays[j]["width"], bays[j]["height"]
            for oi, sh in enumerate(B[i]["shape"]):
                xs = [v[0] for lay in sh["layers"] for v in lay]
                ys = [v[1] for lay in sh["layers"] for v in lay]
                x0, x1 = min(xs), max(xs); y0, y1 = min(ys), max(ys)
                lo_x = int(math.ceil(-x0)); hi_x = int(math.floor(Wb - x1))
                lo_y = int(math.ceil(-y0)); hi_y = int(math.floor(Hb - y1))
                if hi_x < lo_x or hi_y < lo_y:
                    continue
                for xx in range(lo_x, hi_x + 1, GRID):
                    for yy in range(lo_y, hi_y + 1, GRID):
                        for e in ents:
                            CAND[i].append((j, oi, xx, yy, e))
for i in range(n):
    CAND[i] = sorted(set(CAND[i]))

flatc, idx = [], []
for i in range(n):
    for q, (j, oi, x, y, e) in enumerate(CAND[i]):
        flatc += [i, j, oi, x, y, e]
        idx.append((i, q))
eng.set_solution([0] * (n * 7)); eng.build_occ(hor)     # empty board: pure geometry
t0 = time.time()
pairs = set()
if os.environ.get("GM_LAZY", "0") != "1":
    # the upfront build is quadratic in the vocabulary; with lazy
    # separation it is pure waste (measured: a GRID vocabulary made this
    # call, not the MIP, the wall-clock bottleneck).
    prs = eng.pair_conflicts(flatc)
    for t in range(0, len(prs), 2):
        a, b = idx[prs[t]], idx[prs[t + 1]]
        if a[0] != b[0]:
            pairs.add((a, b))
conf_time = time.time() - t0

if SOLVER == "cpsat":
    res = {"board": os.path.basename(prob_path), "solver": "cpsat",
           "candidates": sum(len(CAND[i]) for i in range(n)),
           "best_input": best_obj, "rounds": []}
    m2 = _cp.CpModel()
    Z = {(i, q): m2.NewBoolVar("z%d_%d" % (i, q))
         for i in range(n) for q in range(len(CAND[i]))}
    for i in range(n):
        m2.AddExactlyOne([Z[i, q] for q in range(len(CAND[i]))])
    cost = {}
    for i in range(n):
        for q, c in enumerate(CAND[i]):
            cost[i, q] = int(w1 * max(0, c[4] + P[i] - D[i])
                             + w3 * (SMAX[i] - PREF[i][c[0]]))
    m2.Minimize(sum(cost[k] * Z[k] for k in Z))     # Z2 omitted: <= w2*range
    known = set()
    sol_flat = None
    for rnd in range(ROUNDS):
        sv = _cp.CpSolver()
        sv.parameters.max_time_in_seconds = TL
        sv.parameters.num_search_workers = 8
        st = sv.Solve(m2)
        if st not in (_cp.OPTIMAL, _cp.FEASIBLE):
            res["rounds"].append({"r": rnd, "status": sv.StatusName(st)})
            break
        flat = [0] * (n * 7)
        chosen = []
        for i in range(n):
            for q, c in enumerate(CAND[i]):
                if sv.Value(Z[i, q]):
                    flat[i*7:i*7+7] = [c[0], c[1], c[2], c[3], c[4],
                                       c[4] + P[i], 1]
                    chosen.append((i, q, c))
                    break
        eng.set_solution(flat); eng.build_occ(hor)
        bad = [i for i in range(n) if not eng.vp_ok(i)]
        res["rounds"].append({"r": rnd, "obj": round(sv.ObjectiveValue()),
                              "bound": round(sv.BestObjectiveBound()),
                              "engine_obj": eng.objective()[3],
                              "vp_bad": len(bad)})
        if not bad:
            sol_flat = flat
            break
        sub, sidx = [], []
        for i, q, c in chosen:
            sub += [i, c[0], c[1], c[2], c[3], c[4]]
            sidx.append((i, q))
        eng.set_solution([0] * (n * 7)); eng.build_occ(hor)
        pr2 = eng.pair_conflicts(sub)
        added = 0
        for t in range(0, len(pr2), 2):
            a, b = sidx[pr2[t]], sidx[pr2[t + 1]]
            if a[0] == b[0] or (a, b) in known:
                continue
            known.add((a, b)); known.add((b, a))
            m2.AddBoolOr([Z[a].Not(), Z[b].Not()]); added += 1
        res["rounds"][-1]["cuts_added"] = added
        if added == 0:
            break
    if sol_flat is not None:
        eng.set_solution(sol_flat); eng.build_occ(hor)
        sol = M._recs_to_sol(M._extract(eng, n))
        chk = M._check(p, sol)
        res["final_obj"] = eng.objective()[3]
        res["checker_feasible"] = None if chk is None else bool(chk.get("feasible"))
        res["gain_vs_best_input_pct"] = round(
            100 * (best_obj - res["final_obj"]) / best_obj, 3)
        json.dump(sol, open(out_path, "w"))
    print(json.dumps(res))
    raise SystemExit

mdl = gp.Model(env=gp.Env(params={"OutputFlag": 0}))
z = {(i, q): mdl.addVar(vtype=GRB.BINARY)
     for i in range(n) for q in range(len(CAND[i]))}
for i in range(n):
    mdl.addConstr(gp.quicksum(z[i, q] for q in range(len(CAND[i]))) == 1)
zmax = mdl.addVar(lb=0.0); zmin = mdl.addVar(lb=0.0)
for j in range(m):
    ld = gp.quicksum(u[j] * L[i] * z[i, q] for i in range(n)
                     for q, c in enumerate(CAND[i]) if c[0] == j)
    mdl.addConstr(zmax >= ld); mdl.addConstr(zmin <= ld)
# AREA CAPACITY ROWS -- **NOT VALID FOR THIS PROBLEM**, default OFF.
# The intuition (residents of a bay on a day cannot exceed its area) is
# false here: non-overlap is required only between the SAME LAYER of
# two blocks, so interlocking non-convex pieces legally overlap in the
# top view. This repo measured exactly that -- bbox utilisation 103%
# while polygon utilisation is 58-65% (repair.hpp) -- i.e. feasible
# boards DO exceed the bbox-area sum. Switching it on cut off the
# incumbent itself (P3 and P4 both returned sol_count 0, 08-04).
# Kept behind a flag only as a documented dead end; a valid version
# would need per-LAYER polygon areas, not bbox areas, and would still
# only be tight for boards without interlock.
if os.environ.get("GM_AREA", "0") == "1":      # INVALID here -- see below
    # The coefficient must be a LOWER bound on the block's true
    # footprint, otherwise the cut is not valid and can cut off the
    # incumbent itself -- measured 08-04: with orientation-0 areas both
    # P3 and P4 returned sol_count 0, i.e. even the warm start was
    # excluded. Use the minimum bbox area over orientations.
    areaOf = []
    for i in range(n):
        best = None
        for sh in B[i]["shape"]:
            xs_ = [v[0] for lay in sh["layers"] for v in lay]
            ys_ = [v[1] for lay in sh["layers"] for v in lay]
            a = (max(xs_) - min(xs_)) * (max(ys_) - min(ys_))
            best = a if best is None else min(best, a)
        areaOf.append(best)
    span = {}
    for i in range(n):
        for q, c in enumerate(CAND[i]):
            for t in range(c[4], c[4] + P[i]):
                span.setdefault((c[0], t), []).append((i, q))
    for (j, t), lst in span.items():
        if len(lst) < 2:
            continue
        mdl.addConstr(gp.quicksum(areaOf[i] * z[i, q] for i, q in lst)
                      <= bays[j]["width"] * bays[j]["height"])

obj = gp.quicksum((w1 * max(0, c[4] + P[i] - D[i])
                   + w3 * (SMAX[i] - PREF[i][c[0]])) * z[i, q]
                  for i in range(n) for q, c in enumerate(CAND[i]))
mdl.setObjective(obj + w2 * (zmax - zmin), GRB.MINIMIZE)
LAZY = os.environ.get("GM_LAZY", "0") == "1"
if not LAZY:
    for (i, q), (k, r) in pairs:
        mdl.addConstr(z[i, q] + z[k, r] <= 1)
else:
    # keep the pair set for bookkeeping but let the separation loop add
    # them: a big vocabulary has quadratically many pairs, and only the
    # ones an integer solution actually violates are ever needed.
    pairs = set()
# warm start from the incumbent
for i in range(n):
    cur = (best_flat[i*7], best_flat[i*7+1], best_flat[i*7+2],
           best_flat[i*7+3], best_flat[i*7+4])
    for q, c in enumerate(CAND[i]):
        z[i, q].Start = 1.0 if c == cur else 0.0
mdl.Params.MIPFocus = 1

CB = os.environ.get("GM_CB", "0") == "1"
if CB:
    # Proper separation: enforce conflicts INSIDE the search tree. The
    # outer round loop restarts the whole solve after each batch of
    # cuts, so it re-pays the tree every time and converges in ~250
    # cuts/round; a lazy callback adds them at every integer node and
    # keeps one tree. The oracle is the engine's pair model, which the
    # 08-04 fix made sound, so a solution that survives separation is
    # feasible by construction.
    mdl.Params.LazyConstraints = 1
    mdl.Params.TimeLimit = float(os.environ.get("GM_CBTL", "900"))
    state = {"cuts": 0, "sols": 0, "best": None}

    def sep(model, where):
        if where != GRB.Callback.MIPSOL:
            return
        vals = model.cbGetSolution(model._z)
        sub, sidx = [], []
        for (i, q), v in zip(model._keys, vals):
            if v > 0.5:
                c = CAND[i][q]
                sub += [i, c[0], c[1], c[2], c[3], c[4]]
                sidx.append((i, q))
        pr = eng.pair_conflicts(sub)
        added = 0
        for t in range(0, len(pr), 2):
            a, b = sidx[pr[t]], sidx[pr[t + 1]]
            if a[0] == b[0]:
                continue
            model.cbLazy(model._z[model._pos[a]] + model._z[model._pos[b]] <= 1)
            added += 1
        state["cuts"] += added
        state["sols"] += 1
        if added == 0:
            ob = model.cbGet(GRB.Callback.MIPSOL_OBJ)
            if state["best"] is None or ob < state["best"]:
                state["best"] = ob

    keys = [(i, q) for i in range(n) for q in range(len(CAND[i]))]
    mdl._keys = keys
    mdl._z = [z[k] for k in keys]
    mdl._pos = {k: t for t, k in enumerate(keys)}
    eng.set_solution([0] * (n * 7)); eng.build_occ(hor)
    mdl.optimize(sep)
    out = {"board": os.path.basename(prob_path), "mode": "callback",
           "candidates": sum(len(CAND[i]) for i in range(n)),
           "best_input": best_obj, "lazy_cuts": state["cuts"],
           "int_sols": state["sols"], "status": mdl.Status,
           "sol_count": mdl.SolCount}
    if mdl.SolCount:
        flat = [0] * (n * 7)
        for i in range(n):
            for q, c in enumerate(CAND[i]):
                if z[i, q].X > 0.5:
                    flat[i*7:i*7+7] = [c[0], c[1], c[2], c[3], c[4],
                                       c[4] + P[i], 1]
                    break
        eng.set_solution(flat); eng.build_occ(hor)
        out["vp_bad"] = sum(1 for i in range(n) if not eng.vp_ok(i))
        out["engine_obj"] = eng.objective()[3]
        out["mip_obj"] = round(mdl.ObjVal)
        out["bound"] = round(mdl.ObjBound)
        if out["vp_bad"] == 0:
            sol = M._recs_to_sol(M._extract(eng, n))
            chk = M._check(p, sol)
            out["checker_feasible"] = None if chk is None else bool(chk.get("feasible"))
            out["checker_obj"] = None if chk is None else chk.get("objective")
            out["gain_vs_best_input_pct"] = round(
                100 * (best_obj - out["engine_obj"]) / best_obj, 3)
            json.dump(sol, open(out_path, "w"))
    print(json.dumps(out))
    raise SystemExit

res = {"board": os.path.basename(prob_path),
       "candidates": sum(len(CAND[i]) for i in range(n)),
       "conflict_pairs": len(pairs), "conflict_build_s": round(conf_time, 2),
       "best_input": best_obj, "rounds": []}
sol_flat = None
for rnd in range(ROUNDS):
    mdl.Params.TimeLimit = TL
    mdl.optimize()
    if mdl.SolCount == 0:
        res["rounds"].append({"r": rnd, "status": mdl.Status, "sol": 0})
        break
    flat = [0] * (n * 7)
    for i in range(n):
        for q, c in enumerate(CAND[i]):
            if z[i, q].X > 0.5:
                flat[i*7:i*7+7] = [c[0], c[1], c[2], c[3], c[4], c[4] + P[i], 1]
                break
    eng.set_solution(flat); eng.build_occ(hor)
    bad = [i for i in range(n) if not eng.vp_ok(i)]
    obj_v = eng.objective()[3]
    res["rounds"].append({"r": rnd, "mip_obj": round(mdl.ObjVal),
                          "engine_obj": obj_v, "vp_bad": len(bad),
                          "gap": round(mdl.MIPGap, 4)})
    if not bad:
        sol_flat = flat
        break
    # separate: re-derive the conflicts among the CHOSEN placements only
    sub, sidx = [], []
    for i in range(n):
        for q, c in enumerate(CAND[i]):
            if z[i, q].X > 0.5:
                sub += [i, c[0], c[1], c[2], c[3], c[4]]
                sidx.append((i, q))
                break
    eng.set_solution([0] * (n * 7)); eng.build_occ(hor)
    pr2 = eng.pair_conflicts(sub)
    added = 0
    for t in range(0, len(pr2), 2):
        a, b = sidx[pr2[t]], sidx[pr2[t + 1]]
        if a[0] == b[0] or (a, b) in pairs or (b, a) in pairs:
            continue
        pairs.add((a, b)); mdl.addConstr(z[a] + z[b] <= 1); added += 1
    res["rounds"][-1]["cuts_added"] = added
    if added == 0:
        break

if sol_flat is not None:
    eng.set_solution(sol_flat); eng.build_occ(hor)
    sol = M._recs_to_sol(M._extract(eng, n))
    chk = M._check(p, sol)
    res["final_obj"] = eng.objective()[3]
    res["checker_feasible"] = None if chk is None else bool(chk.get("feasible"))
    res["checker_obj"] = None if chk is None else chk.get("objective")
    res["gain_vs_best_input_pct"] = round(
        100 * (best_obj - res["final_obj"]) / best_obj, 3)
    json.dump(sol, open(out_path, "w"))
print(json.dumps(res))
