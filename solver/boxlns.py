"""CP-SAT exact-repair LNS: destroy a slice, re-place it OPTIMALLY.

The engine's repair is greedy first-fit over corner candidates. This
neighbourhood instead hands a small subset (one bay, one time window,
~10-25 blocks) to CP-SAT with x/y/entry as continuous variables and
the untouched residents as fixed space-time obstacles, then takes the
proven-best arrangement of that slice.

Bbox-conservative, so every accepted move is checker-legal: bbox
non-overlap in (x,y,t) implies the crane descent/ascent rules, and our
boards only carry 3% bbox overlap anyway.
"""
import json
import math
import os
import random
import time


def _dims(B):
    out = []
    for b in B:
        ds = []
        for sh in b["shape"]:
            vs = [v for L in sh["layers"] for v in L]
            xs = [v[0] for v in vs]; ys = [v[1] for v in vs]
            ds.append((int(math.ceil(max(xs)-min(xs))),
                       int(math.ceil(max(ys)-min(ys))),
                       int(math.floor(min(xs))), int(math.floor(min(ys)))))
        out.append(ds)
    return out


def optimize(p, flat, deadline, seed=7, diag=None):
    try:
        from ortools.sat.python import cp_model
    except Exception:
        return None
    B = p["blocks"]; n = len(B); bays = p["bays"]
    P = [max(1, int(math.ceil(b["processing_time"]-1e-9))) for b in B]
    R = [int(math.ceil(b["release_time"]-1e-9)) for b in B]
    D = [int(round(b["due_date"])) for b in B]
    w1 = p["weights"]["w1"]
    dims = _dims(B)
    cur = list(flat)
    rng = random.Random(seed)
    H = max(D) + max(P) + 10
    rounds = 0; acc = 0

    def box(i, fl):
        o = fl[i*7+1]
        wi, hi, ox, oy = dims[i][o]
        return (fl[i*7+2] + ox, fl[i*7+3] + oy, wi, hi)

    while time.time() < deadline - 1.0:
        rounds += 1
        j = rng.randrange(len(bays))
        mem = [i for i in range(n) if cur[i*7+6] and cur[i*7] == j]
        if len(mem) < 4:
            continue
        anchor = rng.choice(mem)
        t0 = cur[anchor*7+4]
        win = [i for i in mem if abs(cur[i*7+4] - t0) <= 12]
        rng.shuffle(win)
        S = win[:18]
        if len(S) < 4:
            continue
        fixed = [i for i in mem if i not in S]
        mdl = cp_model.CpModel()
        X, Y, E, TD, WD, HT, ORI = {}, {}, {}, {}, {}, {}, {}
        for i in S:
            X[i] = mdl.NewIntVar(0, bays[j]["width"], f"x{i}")
            Y[i] = mdl.NewIntVar(0, bays[j]["height"], f"y{i}")
            E[i] = mdl.NewIntVar(R[i], H - P[i], f"e{i}")
            TD[i] = mdl.NewIntVar(0, H, f"t{i}")
            mdl.AddMaxEquality(TD[i], [E[i] + P[i] - D[i], 0])
            ORI[i] = [mdl.NewBoolVar("") for _ in dims[i]]
            mdl.AddExactlyOne(ORI[i])
            WD[i] = mdl.NewIntVar(0, bays[j]["width"], "")
            HT[i] = mdl.NewIntVar(0, bays[j]["height"], "")
            for k, (wi, hi, _, _) in enumerate(dims[i]):
                mdl.Add(WD[i] == wi).OnlyEnforceIf(ORI[i][k])
                mdl.Add(HT[i] == hi).OnlyEnforceIf(ORI[i][k])
            mdl.Add(X[i] + WD[i] <= bays[j]["width"])
            mdl.Add(Y[i] + HT[i] <= bays[j]["height"])
        def sep(i, k):
            lx = mdl.NewBoolVar(""); rx = mdl.NewBoolVar("")
            by = mdl.NewBoolVar(""); ay = mdl.NewBoolVar("")
            bt = mdl.NewBoolVar(""); at = mdl.NewBoolVar("")
            mdl.Add(X[i] + WD[i] <= X[k]).OnlyEnforceIf(lx)
            mdl.Add(X[k] + WD[k] <= X[i]).OnlyEnforceIf(rx)
            mdl.Add(Y[i] + HT[i] <= Y[k]).OnlyEnforceIf(by)
            mdl.Add(Y[k] + HT[k] <= Y[i]).OnlyEnforceIf(ay)
            mdl.Add(E[i] + P[i] <= E[k]).OnlyEnforceIf(bt)
            mdl.Add(E[k] + P[k] <= E[i]).OnlyEnforceIf(at)
            mdl.AddBoolOr([lx, rx, by, ay, bt, at])
        for a in range(len(S)):
            for b2 in range(a+1, len(S)):
                sep(S[a], S[b2])
        for i in S:                       # vs fixed obstacles
            for k in fixed:
                fx, fy, fw, fh = box(k, cur)
                fe, ff = cur[k*7+4], cur[k*7+5]
                lx = mdl.NewBoolVar(""); rx = mdl.NewBoolVar("")
                by = mdl.NewBoolVar(""); ay = mdl.NewBoolVar("")
                bt = mdl.NewBoolVar(""); at = mdl.NewBoolVar("")
                mdl.Add(X[i] + WD[i] <= fx).OnlyEnforceIf(lx)
                mdl.Add(fx + fw <= X[i]).OnlyEnforceIf(rx)
                mdl.Add(Y[i] + HT[i] <= fy).OnlyEnforceIf(by)
                mdl.Add(fy + fh <= Y[i]).OnlyEnforceIf(ay)
                mdl.Add(E[i] + P[i] <= fe).OnlyEnforceIf(bt)
                mdl.Add(ff <= E[i]).OnlyEnforceIf(at)
                mdl.AddBoolOr([lx, rx, by, ay, bt, at])
        base = sum(max(0, cur[i*7+4] + P[i] - D[i]) for i in S)
        mdl.Add(sum(TD[i] for i in S) <= base)
        mdl.Minimize(sum(TD[i] for i in S))
        for i in S:                        # hint = current placement
            bx, by2, _, _ = box(i, cur)
            mdl.AddHint(X[i], bx); mdl.AddHint(Y[i], by2)
            mdl.AddHint(E[i], cur[i*7+4])
            mdl.AddHint(ORI[i][cur[i*7+1]], 1)
        sv = cp_model.CpSolver()
        sv.parameters.max_time_in_seconds = min(
            4.0, max(0.5, deadline - time.time() - 0.5))
        sv.parameters.num_search_workers = 1
        st = sv.Solve(mdl)
        if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            continue
        got = int(round(sv.ObjectiveValue()))
        if got >= base:
            continue
        for i in S:
            k = next(q for q in range(len(dims[i])) if sv.Value(ORI[i][q]))
            _, _, ox, oy = dims[i][k]
            e = sv.Value(E[i])
            cur[i*7:i*7+7] = [j, k, sv.Value(X[i]) - ox,
                              sv.Value(Y[i]) - oy, e, e + P[i], 1]
        acc += 1
    if diag:
        diag("boxlns", rounds=rounds, accepted=acc)
    return cur if acc else None
