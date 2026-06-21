"""unified_cpsat.py -- JOINT exact model: assignment + placement + timing in ONE CP-SAT solve.

Root cause of weak small-instance results (e.g. prob_4 3x off optimum): the pipeline DECOMPOSES the problem
(search assignment with a per-bay packing oracle, then pack) and the oracle is either fast+inaccurate (beam
under-packs) or accurate+slow (CP-SAT). Neither lets the assignment search efficiently find the optimal
(low-Z3 AND Z1=0-packable) assignment. This module removes the decomposition: one model jointly chooses each
block's (bay, orientation, position, entry) minimizing w1*Z1 + w2*Z2 + w3*Z3, with no-overlap enforced only
between blocks sharing a bay at overlapping times. Intended for SMALL instances where it solves within budget;
falls back (returns None) otherwise so the caller keeps its heuristic solution.
"""
import math
from ortools.sat.python import cp_model
from packer import block_meta


def _cells(orients, W, H):
    out = []
    for (oi, lx0, ly0, lx1, ly1) in orients:
        offlox, offhix = math.floor(lx0), math.ceil(lx1)
        offloy, offhiy = math.floor(ly0), math.ceil(ly1)
        xlo, xhi = math.ceil(-lx0), W - offhix
        ylo, yhi = math.ceil(-ly0), H - offhiy
        if xlo <= xhi and ylo <= yhi:
            out.append((oi, offlox, offhix, offloy, offhiy, xlo, xhi, ylo, yhi))
    return out


def solve_unified(prob, tl=60.0, workers=4, hint_sol=None, verbose=False):
    from solve import build_ops
    B = prob["blocks"]; bays = prob["bays"]; nb = len(bays); n = len(B)
    w = prob.get("weights", {}); w1, w2, w3 = w.get("w1", 1), w.get("w2", 1), w.get("w3", 1)
    MB = {i: block_meta(B[i], i) for i in range(n)}
    for i in MB:
        MB[i]["proc"] = max(1, MB[i]["proc"])
    due = {i: B[i]["due_date"] for i in range(n)}
    rel = {i: B[i]["release_time"] for i in range(n)}
    smax = {i: max(B[i]["bay_preferences"]) for i in range(n)}
    pref = {i: B[i]["bay_preferences"] for i in range(n)}
    Hor = max(rel.values()) + sum(MB[i]["proc"] for i in range(n)) + 1

    cm = cp_model.CpModel()
    # per block: (bay,orient) choice = pick one cell across all fitting (bay,orient); assignment a_ij derived
    inbay = {}                                  # (i,j) -> BoolVar present in bay j
    cellL = {}; cellR = {}; cellB = {}; cellT = {}
    s_ = {}; e_ = {}; t_ = {}; V = {}
    z3_terms = []
    ok_bays = {i: [] for i in range(n)}
    for i in range(n):
        choices = []                            # (bay j, cell tuple, sel BoolVar)
        for j in range(nb):
            cells = _cells(MB[i]["orients"], bays[j]["width"], bays[j]["height"])
            for c in cells:
                choices.append((j, c))
        if not choices:
            return None
        sel = [cm.NewBoolVar(f"sel{i}_{k}") for k in range(len(choices))]
        cm.AddExactlyOne(sel)
        for j in range(nb):
            jb = cm.NewBoolVar(f"in{i}_{j}")
            js = [sel[k] for k, (jj, c) in enumerate(choices) if jj == j]
            if js:
                cm.Add(jb == sum(js)); ok_bays[i].append(j)
            else:
                cm.Add(jb == 0)
            inbay[(i, j)] = jb
            z3_terms.append((jb, smax[i] - pref[i][j]))
        xs = [c[5] for (j, c) in choices] + [c[6] for (j, c) in choices]
        ys = [c[7] for (j, c) in choices] + [c[8] for (j, c) in choices]
        x = cm.NewIntVar(min(xs), max(xs), f"x{i}"); y = cm.NewIntVar(min(ys), max(ys), f"y{i}")
        olx = cm.NewIntVar(min(c[1] for (j, c) in choices), max(c[1] for (j, c) in choices), f"olx{i}")
        ohx = cm.NewIntVar(min(c[2] for (j, c) in choices), max(c[2] for (j, c) in choices), f"ohx{i}")
        oly = cm.NewIntVar(min(c[3] for (j, c) in choices), max(c[3] for (j, c) in choices), f"oly{i}")
        ohy = cm.NewIntVar(min(c[4] for (j, c) in choices), max(c[4] for (j, c) in choices), f"ohy{i}")
        cm.Add(olx == sum(b * c[1] for b, (j, c) in zip(sel, choices)))
        cm.Add(ohx == sum(b * c[2] for b, (j, c) in zip(sel, choices)))
        cm.Add(oly == sum(b * c[3] for b, (j, c) in zip(sel, choices)))
        cm.Add(ohy == sum(b * c[4] for b, (j, c) in zip(sel, choices)))
        for b, (j, c) in zip(sel, choices):
            cm.Add(x >= c[5]).OnlyEnforceIf(b); cm.Add(x <= c[6]).OnlyEnforceIf(b)
            cm.Add(y >= c[7]).OnlyEnforceIf(b); cm.Add(y <= c[8]).OnlyEnforceIf(b)
        s = cm.NewIntVar(rel[i], Hor, f"s{i}"); e = cm.NewIntVar(rel[i] + MB[i]["proc"], Hor, f"e{i}")
        cm.Add(e == s + MB[i]["proc"])
        t = cm.NewIntVar(0, Hor, f"t{i}"); cm.Add(t >= e - due[i])
        cL = cm.NewIntVar(-10**6, 10**6, ""); cm.Add(cL == x + olx)
        cR = cm.NewIntVar(-10**6, 10**6, ""); cm.Add(cR == x + ohx)
        cB = cm.NewIntVar(-10**6, 10**6, ""); cm.Add(cB == y + oly)
        cT = cm.NewIntVar(-10**6, 10**6, ""); cm.Add(cT == y + ohy)
        cellL[i], cellR[i], cellB[i], cellT[i] = cL, cR, cB, cT
        s_[i], e_[i], t_[i] = s, e, t
        V[i] = (x, y, sel, choices)                 # for solution recovery (ref position + orientation)

    # pairwise: same-bay & time-overlap -> spatial separation. If different bays -> trivially ok.
    for a in range(n):
        for b in range(a + 1, n):
            i, k = a, b
            common = set(ok_bays[i]) & set(ok_bays[k])
            if not common:
                continue
            diff = cm.NewBoolVar("")            # blocks in different bays
            same_terms = []
            for j in common:
                sij = cm.NewBoolVar("")
                cm.AddBoolAnd([inbay[(i, j)], inbay[(k, j)]]).OnlyEnforceIf(sij)
                cm.AddBoolOr([inbay[(i, j)].Not(), inbay[(k, j)].Not()]).OnlyEnforceIf(sij.Not())
                same_terms.append(sij)
            # diff == 1  <=>  not in any common bay together
            cm.Add(sum(same_terms) == 0).OnlyEnforceIf(diff)
            cm.Add(sum(same_terms) >= 1).OnlyEnforceIf(diff.Not())
            lx = cm.NewBoolVar(""); rx = cm.NewBoolVar(""); dn = cm.NewBoolVar(""); up = cm.NewBoolVar("")
            bt = cm.NewBoolVar(""); bt2 = cm.NewBoolVar("")
            cm.Add(cellR[i] <= cellL[k]).OnlyEnforceIf(lx)
            cm.Add(cellR[k] <= cellL[i]).OnlyEnforceIf(rx)
            cm.Add(cellT[i] <= cellB[k]).OnlyEnforceIf(dn)
            cm.Add(cellT[k] <= cellB[i]).OnlyEnforceIf(up)
            cm.Add(e_[i] <= s_[k]).OnlyEnforceIf(bt)
            cm.Add(e_[k] <= s_[i]).OnlyEnforceIf(bt2)
            cm.AddBoolOr([diff, lx, rx, dn, up, bt, bt2])

    # Z2 (workload imbalance, scaled) + Z3 (preference)
    areas = [bays[j]["width"] * bays[j]["height"] for j in range(nb)]; avg = sum(areas) / nb
    SCALE = 1000; U = [round(avg / a * SCALE) for a in areas]
    A = []
    for j in range(nb):
        load = sum(B[i]["workload"] * inbay[(i, j)] for i in range(n) if j in ok_bays[i])
        aj = cm.NewIntVar(0, 10**9, f"A{j}"); cm.Add(aj == U[j] * load); A.append(aj)
    Z2 = cm.NewIntVar(0, 10**9, "Z2")
    mx = cm.NewIntVar(0, 10**9, ""); mn = cm.NewIntVar(0, 10**9, "")
    cm.AddMaxEquality(mx, A); cm.AddMinEquality(mn, A); cm.Add(Z2 * SCALE >= mx - mn)
    Z3 = cm.NewIntVar(0, 10**9, "Z3"); cm.Add(Z3 == sum(b * c for b, c in z3_terms))
    cm.Minimize(w1 * sum(t_[i] for i in range(n)) + w2 * Z2 + w3 * Z3)

    if hint_sol is not None:                    # warm start from a feasible heuristic solution
        try:
            for tt, ops in hint_sol["operations"].items():
                for o in ops:
                    if o["type"] == "ENTRY":
                        i = o["block_id"]
                        cm.AddHint(inbay[(i, o["bay_id"])], 1)
        except Exception:
            pass
    sv = cp_model.CpSolver(); sv.parameters.max_time_in_seconds = tl
    sv.parameters.num_search_workers = workers
    st = sv.Solve(cm)
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    place = {}
    for i in range(n):
        xv, yv, sel, choices = V[i]
        ksel = next((k for k in range(len(sel)) if sv.Value(sel[k]) == 1), None)
        if ksel is None:
            return None
        j, cell = choices[ksel]; oi = cell[0]
        place[i] = (j, sv.Value(xv), sv.Value(yv), oi, sv.Value(s_[i]), sv.Value(e_[i]))
    return place, sv.ObjectiveValue(), st
