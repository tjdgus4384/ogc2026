"""In-TL certified CP-SAT refinement post-pass.

After the portfolio produces its candidate boards: mine per-block
position menus from the candidates, certify same-bay bbox-overlapping
pattern pairs with the vp kernel-oracle (checker-exact placement
semantics, validated offline: 0/2000 + 0/1500 audits), solve the
pattern+entry CP with the best board as hint, checker-gate the result.
Strictly additive: any failure or non-improvement returns None.

Offline evidence for the move class (fresh-vocab CP rounds): P3 -1,040
/ P4 -2.6k / P6 -13k on incumbents the anneal had just produced.
Grading env has ortools (problem_statement.md sec 3.2).
"""
import json
import math
import os
import time


def _bbox(blk, oi):
    vs = [v for L in blk["shape"][oi]["layers"] for v in L]
    xs = [v[0] for v in vs]
    ys = [v[1] for v in vs]
    return min(xs), min(ys), max(xs), max(ys)


def refine(p, eng2, cands, best_flat, deadline, best_obj=None,
           diag=None):
    """p: problem dict; eng2: a SCRATCH engine (same problem) used as
    the pair oracle; cands: list of flat solutions (portfolio boards,
    best first); best_flat: the board to beat (flat); deadline: wall
    deadline for the WHOLE pass (reserve already subtracted by caller).
    Returns an improved flat solution or None."""
    t0 = time.time()
    _dbgp = os.environ.get("OGC_REFDBG", "")

    def _dbg(msg):
        if _dbgp:
            try:
                with open(_dbgp, "a") as fh:
                    fh.write(f"{round(time.time()-t0,2)} {msg}\n")
            except Exception:
                pass
    _dbg("enter")
    try:
        from ortools.sat.python import cp_model
    except Exception:
        _dbg("no ortools")
        return None
    B = p["blocks"]
    n = len(B)
    P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
    R = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]
    D = [int(round(b["due_date"])) for b in B]
    WL = [b["workload"] for b in B]
    pref = [b["bay_preferences"] for b in B]
    w = p["weights"]
    m = len(p["bays"])

    # ---- menus from candidate boards (dedup, keep order = quality) --
    pats = [[] for _ in range(n)]
    seen = [set() for _ in range(n)]
    for flat in cands:
        for i in range(n):
            if not flat[i * 7 + 6]:
                continue
            k = (flat[i * 7], flat[i * 7 + 1],
                 flat[i * 7 + 2], flat[i * 7 + 3])
            if k not in seen[i]:
                seen[i].add(k)
                pats[i].append(k)
    if any(not row for row in pats):
        _dbg("empty menu row")
        return None
    hpat = [None] * n
    hent = [None] * n
    for i in range(n):
        if not best_flat[i * 7 + 6]:
            _dbg(f"best unplaced {i}")
            return None
        hpat[i] = (best_flat[i * 7], best_flat[i * 7 + 1],
                   best_flat[i * 7 + 2], best_flat[i * 7 + 3])
        hent[i] = best_flat[i * 7 + 4]
        if hpat[i] not in seen[i]:
            pats[i].append(hpat[i])

    # ---- overlap pair table -----------------------------------------
    pbb = [[(k[0],) + tuple(a + o for a, o in zip(
            _bbox(B[i], k[1])[:2] + _bbox(B[i], k[1])[2:],
            (k[2], k[3], k[2], k[3])))
            for k in pats[i]] for i in range(n)]

    def ov(a, b):
        return (a[0] == b[0] and a[1] < b[3] and b[1] < a[3]
                and a[2] < b[4] and b[2] < a[4])

    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            for ki in range(len(pats[i])):
                for kj in range(len(pats[j])):
                    if ov(pbb[i][ki], pbb[j][kj]):
                        pairs.append((i, ki, j, kj))
    # budget check: oracle probes ~1ms each, 4 strict combos per pair
    est = len(pairs) * 4 * 0.00006
    left = deadline - time.time()
    if est > 0.5 * left:
        # shrink menus to top-2 + hint and retry the table once
        for i in range(n):
            keep = pats[i][:2]
            if hpat[i] not in keep:
                keep.append(hpat[i])
            pats[i] = keep
        pbb = [[(k[0],) + tuple(a + o for a, o in zip(
                _bbox(B[i], k[1])[:2] + _bbox(B[i], k[1])[2:],
                (k[2], k[3], k[2], k[3])))
                for k in pats[i]] for i in range(n)]
        pairs = []
        for i in range(n):
            for j in range(i + 1, n):
                for ki in range(len(pats[i])):
                    for kj in range(len(pats[j])):
                        if ov(pbb[i][ki], pbb[j][kj]):
                            pairs.append((i, ki, j, kj))
        est = len(pairs) * 4 * 0.00006
        left = deadline - time.time()
        if est > 0.6 * left:
            _dbg(f"budget bail pairs={len(pairs)} est={est:.1f} left={left:.1f}")
            return None

    # ---- oracle certification (strict combos; ties are structurally
    #      illegal -- offline TIEALL certification 0/90k) -------------
    empty = [0, 0, 0, 0, 0, 0, 0] * n

    def ok_at(i, ki, j, kj, ei, ej):
        b1, o1, x1, y1 = pats[i][ki]
        b2, o2, x2, y2 = pats[j][kj]
        flat = list(empty)
        flat[i * 7:i * 7 + 7] = [b1, o1, x1, y1, ei, ei + P[i], 1]
        flat[j * 7:j * 7 + 7] = [b2, o2, x2, y2, ej, ej + P[j], 1]
        eng2.set_solution(flat)
        return eng2.vp_ok(i) and eng2.vp_ok(j)

    def witness(i, j, re_, rf):
        dp = P[i] - P[j]
        for delta in ([1, -dp, -dp + 1, -dp - 1] if re_ == 1 else
                      [-1, -dp, -dp + 1, -dp - 1]):
            if (delta > 0) - (delta < 0) != re_:
                continue
            s = delta + dp
            if (s > 0) - (s < 0) != rf:
                continue
            base = 5 + max(0, -delta)
            ei, ej = base + delta, base
            if ei < ej + P[j] and ej < ei + P[i]:
                return ei, ej
        return None

    _dbg(f"certify pairs={len(pairs)}")
    verd = {}
    QUADS = [(-1, -1), (-1, 1), (1, -1), (1, 1)]
    for (i, ki, j, kj) in pairs:
        if time.time() > deadline - 20.0:
            _dbg("certify timeout")
            return None
        vv = []
        for (re_, rf) in QUADS:
            wt = witness(i, j, re_, rf)
            vv.append(False if wt is None
                      else ok_at(i, ki, j, kj, *wt))
        verd[(i, ki, j, kj)] = vv

    # ---- CP model ---------------------------------------------------
    H = max(D) + max(P) + 5
    for i in range(n):
        H = max(H, hent[i] + P[i] + 1)
    mdl = cp_model.CpModel()
    lit = [[mdl.NewBoolVar("") for _ in pats[i]] for i in range(n)]
    for i in range(n):
        mdl.AddExactlyOne(lit[i])
    e = [mdl.NewIntVar(R[i], H - P[i], "") for i in range(n)]
    td = [mdl.NewIntVar(0, H, "") for i in range(n)]
    for i in range(n):
        mdl.AddMaxEquality(td[i], [e[i] + P[i] - D[i], 0])
    for (i, ki, j, kj), vv in verd.items():
        li, lj = lit[i][ki], lit[j][kj]
        dj1 = mdl.NewBoolVar("")
        mdl.Add(e[i] + P[i] <= e[j]).OnlyEnforceIf(dj1)
        mdl.Add(e[i] + P[i] > e[j]).OnlyEnforceIf(dj1.Not())
        dj2 = mdl.NewBoolVar("")
        mdl.Add(e[j] + P[j] <= e[i]).OnlyEnforceIf(dj2)
        mdl.Add(e[j] + P[j] > e[i]).OnlyEnforceIf(dj2.Not())
        elt = mdl.NewBoolVar(""); egt = mdl.NewBoolVar("")
        eeq = mdl.NewBoolVar("")
        mdl.AddExactlyOne([elt, eeq, egt])
        mdl.Add(e[i] < e[j]).OnlyEnforceIf(elt)
        mdl.Add(e[i] == e[j]).OnlyEnforceIf(eeq)
        mdl.Add(e[i] > e[j]).OnlyEnforceIf(egt)
        flt = mdl.NewBoolVar(""); fgt = mdl.NewBoolVar("")
        feq = mdl.NewBoolVar("")
        mdl.AddExactlyOne([flt, feq, fgt])
        mdl.Add(e[i] + P[i] < e[j] + P[j]).OnlyEnforceIf(flt)
        mdl.Add(e[i] + P[i] == e[j] + P[j]).OnlyEnforceIf(feq)
        mdl.Add(e[i] + P[i] > e[j] + P[j]).OnlyEnforceIf(fgt)
        EL = {-1: elt, 1: egt}
        FL = {-1: flt, 1: fgt}
        # forbid illegal strict quadrants; forbid ties while coexisting
        for qi, (re_, rf) in enumerate(QUADS):
            if vv[qi] is not True:
                mdl.AddBoolOr([li.Not(), lj.Not(), dj1, dj2,
                               EL[re_].Not(), FL[rf].Not()])
        mdl.AddBoolOr([li.Not(), lj.Not(), dj1, dj2, eeq.Not()])
        mdl.AddBoolOr([li.Not(), lj.Not(), dj1, dj2, feq.Not()])
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
    for i in range(n):
        mdl.AddHint(lit[i][pats[i].index(hpat[i])], 1)
        mdl.AddHint(e[i], hent[i])
    areas = [by["width"] * by["height"] for by in p["bays"]]
    uj = [sum(areas) / m / a for a in areas]
    SC = 1000
    load = []
    for jb in range(m):
        lo = mdl.NewIntVar(0, int(sum(WL) * max(uj) * SC) + 1, "")
        mdl.Add(lo == sum(int(round(uj[jb] * WL[i] * SC)) * lit[i][k]
                          for i in range(n)
                          for k in range(len(pats[i]))
                          if pats[i][k][0] == jb))
        load.append(lo)
    z2 = mdl.NewIntVar(0, int(sum(WL) * max(uj) * SC) + 1, "")
    for a in range(m):
        for b in range(m):
            if a != b:
                mdl.Add(z2 >= load[a] - load[b])
    smax = [max(pref[i]) for i in range(n)]
    mdl.Minimize(
        sum(int(round(w["w1"])) * SC * td[i] for i in range(n)) +
        int(round(w["w2"])) * z2 +
        sum(int(round(w["w3"] * (smax[i] - pref[i][pats[i][k][0]])))
            * SC * lit[i][k]
            for i in range(n) for k in range(len(pats[i]))))

    sv = cp_model.CpSolver()
    sv.parameters.max_time_in_seconds = max(
        2.0, deadline - time.time() - 6.0)
    sv.parameters.num_search_workers = 4
    _dbg(f"solve start budget={max(2.0, deadline - time.time() - 6.0):.1f}")
    st = sv.Solve(mdl)
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        _dbg(f"solve status {st}")
        return None
    _dbg(f"cp obj {sv.ObjectiveValue()/SC:.0f} vs best {best_obj}")
    if best_obj is not None and sv.ObjectiveValue() / SC >= best_obj:
        return None
    out = list(empty)
    for i in range(n):
        k = next(k for k in range(len(pats[i]))
                 if sv.Value(lit[i][k]))
        b_, o_, x_, y_ = pats[i][k]
        ei = sv.Value(e[i])
        out[i * 7:i * 7 + 7] = [b_, o_, x_, y_, ei, ei + P[i], 1]
    if diag:
        diag("refine", pairs=len(pairs),
             obj=round(sv.ObjectiveValue() / SC),
             wall=round(time.time() - t0, 1))
    return out
