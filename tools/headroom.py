#!/usr/bin/env python3
"""Measure the REALIZABLE scheduling headroom -- and build the board that has it.

Where the instruments stand. The greedy rebuild is broken (control: it cannot
rebuild our own board, Z1 169 -> 605). The exact per-bay realizer is validated
(board mode reproduces our bays exactly, OPTIMAL in seconds) and its first
verdicts on the max-greed reschedule are INFEASIBLE (bay 1, proven in 8s) or
UNKNOWN (bays 0/2, timeout): asking for ALL the headroom at once produces
schedules geometry refuses and CP cannot navigate.

So ask for less, and ask near the witness. For each bay, sweep a tardiness
budget k upward from 0 toward our own Z1; at each k the master picks, among all
schedules with sum(t) <= k under the bay's demonstrated area peak and entry
count cap, the one CLOSEST to our own entry days (min sum |s_i - e_ours_i|).
Nearness to a realizable witness buys realizability, and the witness anchors
(our own positions, hinted to CP) buy solver speed. The first k that realizes
is that bay's REALIZABLE headroom -- not a relaxation's promise, an actual
packed bay, vp-checked block by block.

Bays are independent given schedules, so the per-bay winners COMPOSE: apply
every bay's best realized schedule to the pipeline board, score the whole
thing, and run the official checker on it. The output is not a verdict but a
board.

    python3 tools/headroom.py prob_4 TL [seed]
"""
import json
import math
import os
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
SOLVER = "builds/pc2"          # correct same-day pair model


def poly_area(v):
    s = 0.0
    for k in range(len(v)):
        x1, y1 = v[k]
        x2, y2 = v[(k + 1) % len(v)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2


def main():
    prob_id = sys.argv[1]
    tl = float(sys.argv[2])
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 7
    sys.path.insert(0, str(REPO / SOLVER))
    os.environ["OGC_SEED"] = str(seed)
    import myalgorithm as ma
    from ortools.sat.python import cp_model as cp
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    B, bays, W = prob["blocks"], prob["bays"], prob["weights"]
    n, m = len(B), len(bays)

    sol = ma.algorithm(prob, timelimit=tl)
    eng, D, P = ma._fresh(prob, 4)
    st = [0] * (n * 7)
    for day, ops in sol["operations"].items():
        for op in ops:
            if op["type"] == "ENTRY":
                i = op["block_id"]
                st[i * 7:i * 7 + 7] = [op["bay_id"], op["orient_idx"], op["x"],
                                       op["y"], int(day), int(day) + P[i], 1]
    eng.set_solution(st)
    eng.build_occ(eng.horizon())
    base = eng.objective()
    R = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]
    AR = [int(round(max(poly_area(l) for l in B[i]["shape"][st[i * 7 + 1]]["layers"])))
          for i in range(n)]
    H = max(D) + 64
    print(f"{prob_id}: board {base[3]:,.0f}  Z1={base[0]:.0f}"
          f"   (TL {tl:.0f}s, seed {seed})")

    def schedule_at(mine, k):
        """closest-to-ours schedule with bay tardiness <= k, or None."""
        peak = max(sum(AR[i] for i in mine
                       if st[i * 7 + 4] <= t < st[i * 7 + 5]) for t in range(H))
        ccap = max(sum(1 for i in mine if st[i * 7 + 4] == t) for t in range(H))
        mdl = cp.CpModel()
        sv_, iv, pu, tv = {}, [], [], []
        dist = []
        for i in mine:
            s_ = mdl.NewIntVar(R[i], H - P[i], f"s{i}")
            e_ = mdl.NewIntVar(R[i], H, f"e{i}")
            t_ = mdl.NewIntVar(0, H, f"t{i}")
            mdl.Add(e_ == s_ + P[i])
            mdl.Add(t_ >= e_ - D[i])
            iv.append(mdl.NewIntervalVar(s_, P[i], e_, f"iv{i}"))
            pu.append(mdl.NewFixedSizeIntervalVar(s_, 1, f"pu{i}"))
            d_ = mdl.NewIntVar(0, H, f"d{i}")
            mdl.AddAbsEquality(d_, s_ - st[i * 7 + 4])
            dist.append(d_)
            sv_[i] = s_
            tv.append(t_)
        mdl.AddCumulative(iv, [AR[i] for i in mine], peak)
        mdl.AddCumulative(pu, [1] * len(mine), max(1, ccap))
        mdl.Add(sum(tv) <= k)
        mdl.Minimize(sum(dist))
        sv = cp.CpSolver()
        sv.parameters.max_time_in_seconds = 60.0
        sv.parameters.num_search_workers = 8
        if sv.Solve(mdl) not in (cp.OPTIMAL, cp.FEASIBLE):
            return None
        return {i: sv.Value(sv_[i]) for i in mine}

    def realize(j, mine, sched):
        """exact CSP; returns {i: candidate} or None."""
        W_, H_ = bays[j]["width"], bays[j]["height"]
        CAND, flat, idx = {}, [], []
        for i in mine:
            cs = []
            for oi, sh in enumerate(B[i]["shape"]):
                xs = [v[0] for lay in sh["layers"] for v in lay]
                ys = [v[1] for lay in sh["layers"] for v in lay]
                xlo = int(math.ceil(-min(xs) - 1e-9))
                ylo = int(math.ceil(-min(ys) - 1e-9))
                xhi = int(math.floor(W_ - max(xs) + 1e-9))
                yhi = int(math.floor(H_ - max(ys) + 1e-9))
                for x in range(xlo, xhi + 1, 2):
                    for y in range(ylo, yhi + 1, 2):
                        cs.append((j, oi, x, y, sched[i]))
            LIM = 90
            if len(cs) > LIM:
                step = len(cs) / LIM
                cs = [cs[int(q * step)] for q in range(LIM)]
            wit = (j, st[i * 7 + 1], st[i * 7 + 2], st[i * 7 + 3], sched[i])
            if wit not in cs:
                cs.append(wit)          # our own anchor, at the new day
            CAND[i] = cs
            for q, c in enumerate(cs):
                flat += [i, c[0], c[1], c[2], c[3], c[4]]
                idx.append((i, q))
        prs = eng.pair_conflicts(flat)
        mdl = cp.CpModel()
        V = {i: [mdl.NewBoolVar(f"z{i}_{q}") for q in range(len(CAND[i]))]
             for i in mine}
        for i in mine:
            mdl.AddExactlyOne(V[i])
            mdl.AddHint(V[i][len(CAND[i]) - 1], 1)     # start from our anchor
        for q in range(0, len(prs), 2):
            (a1, k1), (a2, k2) = idx[prs[q]], idx[prs[q + 1]]
            mdl.AddBoolOr([V[a1][k1].Not(), V[a2][k2].Not()])
        sv = cp.CpSolver()
        sv.parameters.max_time_in_seconds = 90.0
        sv.parameters.num_search_workers = 8
        if sv.Solve(mdl) not in (cp.OPTIMAL, cp.FEASIBLE):
            return None
        out = {}
        for i in mine:
            for q, c in enumerate(CAND[i]):
                if sv.Value(V[i][q]):
                    out[i] = c
                    break
        return out

    # ---- per-bay k-sweep, ascending: first SAT = best realizable headroom
    best = {}
    for j in range(m):
        mine = [i for i in range(n) if st[i * 7 + 6] and st[i * 7] == j]
        if not mine:
            continue
        ours = sum(max(0, st[i * 7 + 5] - D[i]) for i in mine)
        ks = sorted({0, ours // 4, ours // 2, (3 * ours) // 4})
        got = None
        for k in ks:
            if k >= ours:
                break
            t0 = time.time()
            sch = schedule_at(mine, k)
            if sch is None:
                print(f"   bay {j} (ours {ours}): k={k} no schedule"
                      f"   [{time.time()-t0:.0f}s]")
                continue
            pl = realize(j, mine, sch)
            print(f"   bay {j} (ours {ours}): k={k} "
                  f"{'REALIZED' if pl else 'not realized'}"
                  f"   [{time.time()-t0:.0f}s]")
            if pl:
                got = (k, sch, pl)
                break
        if got:
            best[j] = got

    if not best:
        print("   no bay realized below its own tardiness -- no realizable"
              " headroom at demonstrated density with this instrument")
        return

    # ---- compose the winners into one board and judge it officially
    new = list(st)
    for j, (k, sch, pl) in best.items():
        for i, c in pl.items():
            new[i * 7:i * 7 + 7] = [c[0], c[1], c[2], c[3], c[4],
                                    c[4] + P[i], 1]
    eng.set_solution(new)
    eng.build_occ(eng.horizon())
    bad = sum(1 for i in range(n) if new[i * 7 + 6] and not eng.vp_ok(i))
    o = eng.objective()
    sol2 = ma._recs_to_sol(ma._extract(eng, n), eng.entry_ranks())
    res = ma._check(prob, sol2)
    feas = res.get("feasible") if res else None
    print(f"   composed board: {o[3]:,.0f}  Z1={o[0]:.0f}"
          f"  (was {base[3]:,.0f} / Z1={base[0]:.0f})"
          f"   vp violations {bad}, checker feasible={feas}"
          f"   delta {(o[3]-base[3])/base[3]*100:+.2f}%")


if __name__ == "__main__":
    main()
