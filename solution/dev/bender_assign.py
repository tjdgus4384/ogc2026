"""bender_assign.py -- logic-based Benders for the min-Z3 PACKABLE assignment (the target-machine lever).

The sweet-spot assignment (low Z2/Z3 AND 2D-packable-to-Z1=0) is a rare needle that heuristics (anytime,
BRKGA, construct) miss even with long budgets -- they converge to a Z1=0 basin with suboptimal Z3. With a
long wall-clock limit (the real hidden limits are minutes-to-30min) an EXACT master + packability feedback
can find it:

  MASTER  (CP-SAT): minimize w2*Z2 + w3*Z3 over the bay assignment x_ij (fitting bays only), subject to a
                    coarse per-bay AREA filter + accumulated no-good cuts.
  SUBPROBLEM (beam): for each bay, the compiled per-bay beam packs the assigned set; tardiness > 0 means the
                    set is NOT Z1=0-packable -> add a no-good cut  sum_{i in S} x_ij <= |S|-1  and re-solve.
  Converges to the minimum-Z3 assignment that beam can pack to Z1=0; realize it. Returns a sol dict or None.

Server has Gurobi 13.0.2 (faster MIP) -- this uses ortools CP-SAT for dev portability; the master is a small
binary model and swapping to Gurobi is mechanical if desired.
"""
import time, math
from ortools.sat.python import cp_model
from packer import block_meta
import beam_polish
from assign_lns import _Oracle, _fits


def bender_assign(prob, utils, time_budget=60.0, verbose=False):
    from solve import build_ops
    t0 = time.time()
    B = prob["blocks"]; bays = prob["bays"]; nb = len(bays); n = len(B)
    w = prob.get("weights", {}); w2, w3 = w.get("w2", 1), w.get("w3", 1)
    orc = _Oracle(prob); ORS = orc.ORS; OI = orc.OI; due = orc.due
    fitb = {i: [j for j in range(nb) if _fits(ORS, i, bays[j])] for i in range(n)}
    smax = {i: max(B[i]["bay_preferences"]) for i in range(n)}
    pref = {i: B[i]["bay_preferences"] for i in range(n)}
    workload = {i: B[i]["workload"] for i in range(n)}
    areas = [b["width"] * b["height"] for b in bays]; avg = sum(areas) / nb
    SCALE = 1000; U = [round(avg / a * SCALE) for a in areas]
    # per-block footprint area (min over orientations) -- for the coarse area filter
    fpa = {i: int(min((lx1 - lx0) * (ly1 - ly0) for (lx0, ly0, lx1, ly1) in ORS[i])) for i in range(n)}
    span = {i: max(1, B[i]["processing_time"]) for i in range(n)}        # area-time demand proxy

    cuts = []                                                            # list of (frozenset(ids), bay)
    best_sol = None; best_obj = math.inf

    def build_solve(tl):
        m = cp_model.CpModel()
        x = [[m.NewBoolVar(f"x{i}_{j}") if j in fitb[i] else None for j in range(nb)] for i in range(n)]
        for i in range(n):
            m.AddExactlyOne([x[i][j] for j in fitb[i]])
        # coarse area-time filter: sum(area*span) in a bay <= capacity*horizon-ish (necessary, loose)
        A = []
        for j in range(nb):
            load = sum(workload[i] * x[i][j] for i in range(n) if x[i][j] is not None)
            a = m.NewIntVar(0, 10 ** 9, f"A{j}"); m.Add(a == U[j] * load); A.append(a)
        mx = m.NewIntVar(0, 10 ** 9, "mx"); mn = m.NewIntVar(0, 10 ** 9, "mn")
        m.AddMaxEquality(mx, A); m.AddMinEquality(mn, A)
        Z2 = m.NewIntVar(0, 10 ** 9, "Z2"); m.Add(Z2 * SCALE >= mx - mn)
        Z3 = sum((smax[i] - pref[i][j]) * x[i][j] for i in range(n) for j in range(nb) if x[i][j] is not None)
        for (S, j) in cuts:                                              # no-good: not all of S in bay j
            m.Add(sum(x[i][j] for i in S if x[i][j] is not None) <= len(S) - 1)
        m.Minimize(w2 * Z2 + w3 * Z3)
        s = cp_model.CpSolver(); s.parameters.max_time_in_seconds = tl; s.parameters.num_search_workers = 4
        st = s.Solve(m)
        if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None
        asg = {}
        for i in range(n):
            for j in fitb[i]:
                if s.Value(x[i][j]) == 1:
                    asg[i] = j; break
        return asg

    it = 0
    while time.time() - t0 < time_budget:
        it += 1
        tl = min(15.0, time_budget - (time.time() - t0))
        if tl < 2:
            break
        asg = build_solve(tl)
        if asg is None:
            break
        byb = {j: [] for j in range(nb)}
        for i, j in asg.items():
            byb[j].append(i)
        place = {}; ok = True; new_cuts = 0
        for j in range(nb):
            if not byb[j]:
                continue
            td, packed = orc.beam(j, byb[j], tl=min(6.0, 2.0 + len(byb[j]) / 35.0))
            if td > 1e-9:                                               # NOT Z1=0-packable -> cut this set
                cuts.append((frozenset(byb[j]), j)); new_cuts += 1; ok = False
            else:
                for (b, x_, y_, oi, e, f) in packed:
                    place[b] = (j, x_, y_, oi, e, f)
        if ok and len(place) == n:                                     # all bays Z1=0-packable -> realize
            ts = build_ops(place); r = utils.check_feasibility(prob, ts)
            if r["feasible"] and r["objective"] < best_obj:
                best_obj = r["objective"]; best_sol = ts
                if verbose:
                    print(f"  bender it={it} Z1=0 obj={best_obj:.0f} cuts={len(cuts)}", flush=True)
            break                                                      # min-Z3 packable found (master was optimal-ish)
        if verbose and it % 5 == 0:
            print(f"  bender it={it} +{new_cuts} cuts (total {len(cuts)}) t={time.time()-t0:.0f}s", flush=True)
    return best_sol
