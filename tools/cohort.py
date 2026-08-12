#!/usr/bin/env python3
"""Exact realization of the reschedule: joint cohort placement, day by day.

realize2 could not separate two explanations for its collapse (P4: promised
Z1 49, delivered 599, 77 of 150 late): either the rescheduled day profile is
geometrically infeasible, or the REALIZER is weak -- greedy first-fit places a
synchronized cohort one block at a time, and cohort members block each other
at corners a joint placement would avoid. This separates them.

Simulate the schedule in day order. Each day's entering cohort per bay is
placed JOINTLY: candidates from the kernel's own `feasible_positions` at
exactly that day, mutual exclusion from `pair_conflicts`, selection by CP
maximising the number seated, committed with `place_at` (vp-checked). A member
that cannot seat today is retried tomorrow, accruing real tardiness. So every
placement is exact over the kernel's vocabulary, and what fails is the
schedule -- not the realizer.

This runs on builds/pc2, and must: the reschedule creates same-day entry pairs
in bulk, and the shipped pair model over-rejects exactly those (the tie
defects pc2 repaired; deterministically verified 0 spurious pairs on all four
boards). The correctness repair we shelved as quality-neutral is load-bearing
here.

    verdict: realized Z1 ~ scheduled Z1   the headroom is real and the driver
                                          integration is this very loop;
             realized Z1 >> scheduled     the day profile is geometry-infeasible
                                          over the vocabulary, and the master
                                          needs geometric cuts, not throughput
                                          proxies.

    python3 tools/cohort.py prob_4 TL [seed] [cap_mul]
"""
import json
import math
import os
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
SOLVER = "builds/pc2"          # correct same-day pair model -- see docstring


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
    cap_mul = float(sys.argv[4]) if len(sys.argv) > 4 else 1.0
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
    hz = eng.horizon()
    eng.set_solution(st)
    eng.build_occ(hz)
    base = eng.objective()
    R = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]
    AR = [int(round(max(poly_area(l) for l in B[i]["shape"][st[i * 7 + 1]]["layers"])))
          for i in range(n)]
    H = max(D) + 64
    print(f"{prob_id}: board {base[3]:,.0f}  Z1={base[0]:.0f}"
          f"   (TL {tl:.0f}s, seed {seed}, engine {SOLVER})")

    # ---- per-bay reschedule at our own realized peaks (same model as realize2)
    sched, pred_z1 = {}, 0
    for j in range(m):
        mine = [i for i in range(n) if st[i * 7 + 6] and st[i * 7] == j]
        if not mine:
            continue
        peak = max(sum(AR[i] for i in mine
                       if st[i * 7 + 4] <= t < st[i * 7 + 5]) for t in range(H))
        mdl = cp.CpModel()
        sv_, tv_, iv = {}, [], []
        for i in mine:
            s_ = mdl.NewIntVar(R[i], H - P[i], f"s{i}")
            e_ = mdl.NewIntVar(R[i], H, f"e{i}")
            t_ = mdl.NewIntVar(0, H, f"t{i}")
            mdl.Add(e_ == s_ + P[i])
            mdl.Add(t_ >= e_ - D[i])
            iv.append(mdl.NewIntervalVar(s_, P[i], e_, f"iv{i}"))
            sv_[i] = s_
            tv_.append(t_)
        mdl.AddCumulative(iv, [AR[i] for i in mine],
                          max(1, int(round(cap_mul * peak))))
        mdl.Minimize(sum(tv_))
        sv = cp.CpSolver()
        sv.parameters.max_time_in_seconds = 120.0
        sv.parameters.num_search_workers = 8
        stt = sv.Solve(mdl)
        for i in mine:
            sched[i] = sv.Value(sv_[i]) if stt in (cp.OPTIMAL, cp.FEASIBLE) \
                       else st[i * 7 + 4]
            pred_z1 += max(0, sched[i] + P[i] - D[i])
    print(f"   reschedule: Z1 {base[0]:.0f} -> {pred_z1}")

    # ---- exact day-by-day cohort realization
    eng.set_solution([0] * (n * 7))
    eng.build_occ(hz)
    pending = {}
    for i, d in sched.items():
        pending.setdefault(d, []).append(i)
    t0 = time.time()
    placed = drifted = 0
    day = 0
    while pending and day <= H:
        todo = pending.pop(day, [])
        by_bay = {}
        for i in todo:
            by_bay.setdefault(st[i * 7], []).append(i)
        for j, group in by_bay.items():
            # candidates at exactly this day in this bay
            CAND = {}
            for i in group:
                raw = eng.feasible_positions(i, 12, day)
                cs = [tuple(raw[k:k + 5]) for k in range(0, len(raw), 5)]
                CAND[i] = [c for c in cs if c[0] == j and c[4] == day]
            mdl = cp.CpModel()
            V, use, flat, idx = {}, {}, [], []
            for i in group:
                if not CAND[i]:
                    continue
                V[i] = [mdl.NewBoolVar(f"z{i}_{k}") for k in range(len(CAND[i]))]
                use[i] = mdl.NewBoolVar(f"u{i}")
                mdl.Add(sum(V[i]) == use[i])
                for k, c in enumerate(CAND[i]):
                    flat += [i, c[0], c[1], c[2], c[3], c[4]]
                    idx.append((i, k))
            chosen = {}
            if use:
                prs = eng.pair_conflicts(flat)
                for q in range(0, len(prs), 2):
                    (a1, k1), (a2, k2) = idx[prs[q]], idx[prs[q + 1]]
                    mdl.AddBoolOr([V[a1][k1].Not(), V[a2][k2].Not()])
                mdl.Maximize(sum(use.values()))
                sv = cp.CpSolver()
                sv.parameters.max_time_in_seconds = 10.0
                sv.parameters.num_search_workers = 4
                if sv.Solve(mdl) in (cp.OPTIMAL, cp.FEASIBLE):
                    for i in group:
                        if i in use and sv.Value(use[i]):
                            for k, c in enumerate(CAND[i]):
                                if sv.Value(V[i][k]):
                                    chosen[i] = c
                                    break
            for i in group:
                c = chosen.get(i)
                if c is not None and eng.place_at(i, c[0], c[1], c[2], c[3], c[4]):
                    placed += 1
                else:
                    pending.setdefault(day + 1, []).append(i)   # drift a day
                    drifted += 1
        day += 1
    o = eng.objective()
    sol2 = ma._recs_to_sol(ma._extract(eng, n), eng.entry_ranks())
    res = ma._check(prob, sol2)
    feas = res.get("feasible") if res else None
    unpl = sum(1 for i in range(n) if not eng.get_block(i)[6])
    print(f"   exact-cohort realized: {o[3]:>12,.0f}"
          f"  Z1={o[0]:.0f} Z2={o[1]:.0f} Z3={o[2]:.0f}"
          f"   checker feasible={feas}   [{time.time()-t0:.0f}s]")
    print(f"   {placed} placed, {drifted} day-drifts, {unpl} unplaced"
          f"   vs board {(o[3]-base[3])/base[3]*100:+.2f}%")


if __name__ == "__main__":
    main()
