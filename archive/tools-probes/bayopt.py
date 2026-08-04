"""In-TL per-bay exact decomposition post-pass.

KEY STRUCTURE (verified 08-01): with the bay assignment FIXED, the
objective decomposes exactly.  Z2 (workload imbalance) and Z3
(preference regret) depend only on the assignment, so they are
constants; Z1 is a plain sum of per-block tardiness; and every checker
rule (overlap, crane descent/ascent sweeps) is within-bay.  Therefore
each bay's schedule+packing is an INDEPENDENT subproblem, and merging
per-bay solutions is exactly valid.

That buys three things over whole-board CP (measured refuted at TL):
  * ~m^2-fold fewer pattern pairs per model (P4: 11k -> 3.7k),
  * bays solved in parallel on the 4 grading cores,
  * a pure single-objective model (minimize this bay's tardiness).

Strictly additive: any failure returns None and the caller keeps the
anneal board.
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


def _solve_bay(args):
    """One bay: certified pattern+entry CP minimizing this bay's
    tardiness. Returns {block: [bay, ori, x, y, e]} or None."""
    (P, R, D, pats, verd, hpat, hent, H, budget, blocks) = args
    try:
        from ortools.sat.python import cp_model
    except Exception:
        return None
    mdl = cp_model.CpModel()
    lit = {i: [mdl.NewBoolVar("") for _ in pats[i]] for i in blocks}
    for i in blocks:
        mdl.AddExactlyOne(lit[i])
    e = {i: mdl.NewIntVar(R[i], H - P[i], "") for i in blocks}
    td = {i: mdl.NewIntVar(0, H, "") for i in blocks}
    for i in blocks:
        mdl.AddMaxEquality(td[i], [e[i] + P[i] - D[i], 0])
    QUADS = [(-1, -1), (-1, 1), (1, -1), (1, 1)]
    for (i, ki, j, kj), vv in verd.items():
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
        EL = {-1: elt, 1: egt}
        FL = {-1: flt, 1: fgt}
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
    for i in blocks:
        mdl.AddHint(lit[i][pats[i].index(hpat[i])], 1)
        mdl.AddHint(e[i], hent[i])
    mdl.Minimize(sum(td[i] for i in blocks))
    sv = cp_model.CpSolver()
    sv.parameters.max_time_in_seconds = budget
    sv.parameters.num_search_workers = 1
    st = sv.Solve(mdl)
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    base = sum(max(0, hent[i] + P[i] - D[i]) for i in blocks)
    got = int(round(sv.ObjectiveValue()))
    if got >= base:
        return None
    out = {}
    for i in blocks:
        k = next(k for k in range(len(pats[i])) if sv.Value(lit[i][k]))
        b_, o_, x_, y_ = pats[i][k]
        out[i] = [b_, o_, x_, y_, sv.Value(e[i])]
    return out


def optimize(p, eng2, cands, best_flat, deadline, diag=None):
    """Returns an improved flat solution or None."""
    t0 = time.time()
    B = p["blocks"]
    n = len(B)
    m = len(p["bays"])
    P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
    R = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]
    D = [int(round(b["due_date"])) for b in B]
    dbgp = os.environ.get("OGC_BAYDBG", "")

    def dbg(msg):
        if dbgp:
            try:
                with open(dbgp, "a") as fh:
                    fh.write(f"{round(time.time()-t0,2)} {msg}\n")
            except Exception:
                pass
    dbg("enter")

    hpat, hent, hbay = {}, {}, {}
    for i in range(n):
        if not best_flat[i * 7 + 6]:
            return None
        hbay[i] = best_flat[i * 7]
        hpat[i] = (best_flat[i * 7], best_flat[i * 7 + 1],
                   best_flat[i * 7 + 2], best_flat[i * 7 + 3])
        hent[i] = best_flat[i * 7 + 4]

    # menus: candidate positions RESTRICTED to the block's own bay
    pats = {i: [hpat[i]] for i in range(n)}
    seen = {i: {hpat[i]} for i in range(n)}
    for flat in cands:
        for i in range(n):
            if not flat[i * 7 + 6] or flat[i * 7] != hbay[i]:
                continue
            k = (flat[i * 7], flat[i * 7 + 1],
                 flat[i * 7 + 2], flat[i * 7 + 3])
            if k not in seen[i]:
                seen[i].add(k)
                pats[i].append(k)

    H = max(D) + max(P) + 5
    for i in range(n):
        H = max(H, hent[i] + P[i] + 1)

    by_bay = {}
    for i in range(n):
        by_bay.setdefault(hbay[i], []).append(i)

    # certify pairs inside each bay (vp oracle, 35us/probe measured)
    empty = [0, 0, 0, 0, 0, 0, 0] * n
    QUADS = [(-1, -1), (-1, 1), (1, -1), (1, 1)]

    def ok_at(i, ki, j, kj, ei, ej):
        b1, o1, x1, y1 = pats[i][ki]
        b2, o2, x2, y2 = pats[j][kj]
        fl = list(empty)
        fl[i * 7:i * 7 + 7] = [b1, o1, x1, y1, ei, ei + P[i], 1]
        fl[j * 7:j * 7 + 7] = [b2, o2, x2, y2, ej, ej + P[j], 1]
        eng2.set_solution(fl)
        return eng2.vp_ok(i) and eng2.vp_ok(j)

    def witness(i, j, re_, rf):
        dp = P[i] - P[j]
        for delta in ([1, -dp, -dp + 1, -dp - 1] if re_ == 1 else
                      [-1, -dp, -dp + 1, -dp - 1]):
            if (delta > 0) - (delta < 0) != re_:
                continue
            s2 = delta + dp
            if (s2 > 0) - (s2 < 0) != rf:
                continue
            base = 5 + max(0, -delta)
            ei, ej = base + delta, base
            if ei < ej + P[j] and ej < ei + P[i]:
                return ei, ej
        return None

    def pbb(i, k):
        b_, o_, x_, y_ = pats[i][k]
        x0, y0, x1, y1 = _bbox(B[i], o_)
        return (x0 + x_, y0 + y_, x1 + x_, y1 + y_)

    jobs = []
    for bay, blocks in sorted(by_bay.items()):
        verd = {}
        nskip = 0
        for a in range(len(blocks)):
            for b in range(a + 1, len(blocks)):
                i, j = blocks[a], blocks[b]
                for ki in range(len(pats[i])):
                    bi = pbb(i, ki)
                    for kj in range(len(pats[j])):
                        bj = pbb(j, kj)
                        if not (bi[0] < bj[2] and bj[0] < bi[2]
                                and bi[1] < bj[3] and bj[1] < bi[3]):
                            continue
                        # hint-consistency guard (08-01): drop pairs the
                        # incumbent itself would violate, else the model
                        # is infeasible at its own hint.
                        if hpat[i] == pats[i][ki] and \
                                hpat[j] == pats[j][kj]:
                            hei, hej = hent[i], hent[j]
                            hfi, hfj = hei + P[i], hej + P[j]
                            if hei < hfj and hej < hfi:
                                re_ = (hei > hej) - (hei < hej)
                                rf = (hfi > hfj) - (hfi < hfj)
                                if re_ == 0 or rf == 0:
                                    nskip += 1
                                    continue
                        vv = []
                        for (re_, rf) in QUADS:
                            wt = witness(i, j, re_, rf)
                            vv.append(False if wt is None
                                      else ok_at(i, ki, j, kj, *wt))
                        if hpat[i] == pats[i][ki] and \
                                hpat[j] == pats[j][kj]:
                            hei, hej = hent[i], hent[j]
                            hfi, hfj = hei + P[i], hej + P[j]
                            if hei < hfj and hej < hfi:
                                qi = QUADS.index(
                                    ((hei > hej) - (hei < hej),
                                     (hfi > hfj) - (hfi < hfj)))
                                if vv[qi] is not True:
                                    nskip += 1
                                    continue
                        verd[(i, ki, j, kj)] = vv
            if time.time() > deadline - 12.0:
                dbg("certify timeout")
                return None
        dbg(f"bay {bay}: blocks={len(blocks)} pairs={len(verd)} "
            f"skip={nskip}")
        jobs.append((bay, blocks, verd))

    budget = max(3.0, (deadline - time.time() - 6.0))
    args = [(P, R, D, pats, verd, hpat, hent, H, budget, blocks)
            for (bay, blocks, verd) in jobs]
    results = []
    try:
        import multiprocessing as mp
        with mp.get_context("fork").Pool(min(4, len(args))) as pool:
            results = pool.map(_solve_bay, args)
    except Exception:
        results = [_solve_bay(a) for a in args]

    out = list(best_flat)
    nimp = 0
    for (bay, blocks, _), res in zip(jobs, results):
        if not res:
            continue
        nimp += 1
        for i, (b_, o_, x_, y_, ei) in res.items():
            out[i * 7:i * 7 + 7] = [b_, o_, x_, y_, ei, ei + P[i], 1]
    dbg(f"bays improved: {nimp}/{len(jobs)}")
    if diag:
        diag("bayopt", bays=len(jobs), improved=nimp,
             wall=round(time.time() - t0, 1))
    return out if nimp else None
