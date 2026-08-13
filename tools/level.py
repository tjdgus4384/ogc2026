#!/usr/bin/env python3
"""Can a PUNCTUAL entry vector be levelled to a density we can actually pack?

z1bound proves area forces no tardiness on any board: over every interval, the
mandatory area-days fit. But "fits on average over a window" is not "fits on
every day" -- and the packer only ever realises a bounded density (P4: peak
polygon utilisation 0.635, bbox-sum 1.06). So the precondition for z1=0 splits
in two, and only the first half is cheap:

  (a) scheduling  does an entry vector with e_i in [R_i, D_i - P_i] exist whose
      PER-DAY polygon load stays under the density the packer demonstrably
      reaches? This ignores geometry entirely -- pure resource levelling.
  (b) geometry    can that day-by-day demand actually be laid out under the
      crane rules? No bound is known for this half.

This answers (a) exactly with CP-SAT: minimise the peak day load over all
punctual entry vectors. Reported in both currencies (min-bbox-orientation
polygon area, and that orientation's bbox area), globally and per bay for a
fixed bay assignment read off the shipped board.

    python3 tools/level.py prob_4 prob_5
"""
import json
import math
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent


def parea(L):
    a = 0.0
    for i in range(len(L)):
        x1, y1 = L[i]
        x2, y2 = L[(i + 1) % len(L)]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2


def areas(B):
    PA, BB = [], []
    for b in B:
        best = None
        for sh in b["shape"]:
            L = sh["layers"][0]
            xs = [v[0] for v in L]
            ys = [v[1] for v in L]
            bb = (max(xs) - min(xs)) * (max(ys) - min(ys))
            if best is None or bb < best[1]:
                best = (parea(L), bb)
        PA.append(best[0])
        BB.append(best[1])
    return PA, BB


def level(A, R, D, P, cap, T, scale=1000, tl=120.0):
    """exact min-peak resource levelling: returns (peak/cap, entries)"""
    from ortools.sat.python import cp_model
    n = len(A)
    mdl = cp_model.CpModel()
    # one-hot entry day per block: domains are tiny here (slack 0-10 days)
    S = []
    for i in range(n):
        v = [mdl.NewBoolVar(f"s{i}_{t}") for t in range(R[i], D[i] - P[i] + 1)]
        mdl.AddExactlyOne(v)
        S.append(v)
    ub = int(round(sum(A) * scale))
    pk = mdl.NewIntVar(0, ub, "peak")
    for t in range(T + 1):
        ld = []
        for i in range(n):
            lo = max(R[i], t - P[i] + 1)
            hi = min(D[i] - P[i], t)
            for t0 in range(lo, hi + 1):
                ld.append((int(round(A[i] * scale)), S[i][t0 - R[i]]))
        if ld:
            mdl.Add(sum(c * v for c, v in ld) <= pk)
    mdl.Minimize(pk)
    sv = cp_model.CpSolver()
    sv.parameters.max_time_in_seconds = tl
    sv.parameters.num_search_workers = 8
    st = sv.Solve(mdl)
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, None, None
    return (sv.Value(pk) / scale / cap,
            sv.BestObjectiveBound() / scale / cap,
            st == cp_model.OPTIMAL)


def main():
    ids = sys.argv[1:] or ["prob_4", "prob_5"]
    for pid in ids:
        d = json.load(open(REPO / "data" / "preliminary_test" / f"{pid}.json"))
        B = d["blocks"]
        n = len(B)
        cap = sum(b["width"] * b["height"] for b in d["bays"])
        R = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]
        D = [int(round(b["due_date"])) for b in B]
        P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
        PA, BB = areas(B)
        T = max(D)
        print(f"# {pid} n={n} bays={len(d['bays'])} cap={cap} T={T}")
        for lab, A in (("polygon", PA), ("bbox", BB)):
            v, lb, opt = level(A, R, D, P, cap, T)
            print(f"  min feasible PEAK day utilisation, {lab:>7} currency: "
                  f"{v:.3f}   (bound {lb:.3f}, {'OPTIMAL' if opt else 'time'})")


if __name__ == "__main__":
    main()
