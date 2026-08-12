#!/usr/bin/env python3
"""Realize master2's reschedule through the real kernel.

master2 measured the pure scheduling headroom at our own realized density:
P4's Z1 falls 170 -> 49, which is worth 1,613,293 on the objective -- 1.7x the
entire gap to the all-team minimum -- with bays, orientations, the block set
and the per-bay peak density all exactly ours. Only the entry days move.

P3's realization collapsed because the area relaxation promised densities the
popular bay's geometry refuses. This test is structurally different: the
reschedule never exceeds the peak WE OURSELVES reached in each bay, so the
question is narrower -- does a different DAY PROFILE at the same peak pack?

Procedure: run the pipeline (same seed => same board as master2's run), solve
the per-bay reschedule, then rebuild from empty in ascending scheduled day,
each block forced to its own bay with entry capped at its scheduled day. If
greedy seats it EARLIER than scheduled -- consuming occupancy the schedule
never budgeted, the defect found on P3 -- retime it forward onto its day.
Fallbacks (late in bay, then anywhere) are the schedule's real casualties.

    realized ~ prediction   the headroom is real; wire the reschedule + locked
                            rebuild into the driver as a gated post-pass;
    realized >> prediction  same-peak day profiles are still geometry-limited,
                            and the scheduling axis closes on the Z1 boards
                            the way it closed on P3.

    python3 tools/realize2.py SOLVERDIR prob_4 TL [seed] [cap_mul]
"""
import json
import math
import os
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent


def poly_area(v):
    s = 0.0
    for k in range(len(v)):
        x1, y1 = v[k]
        x2, y2 = v[(k + 1) % len(v)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2


def main():
    solver_dir, prob_id = sys.argv[1], sys.argv[2]
    tl = float(sys.argv[3])
    seed = int(sys.argv[4]) if len(sys.argv) > 4 else 7
    cap_mul = float(sys.argv[5]) if len(sys.argv) > 5 else 1.0
    sys.path.insert(0, str(REPO / solver_dir))
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
          f"   (TL {tl:.0f}s, seed {seed}, cap x{cap_mul})")

    # ---- per-bay optimal entry reschedule at our own realized peak
    sched = {}
    predicted_z1 = 0
    for j in range(m):
        mine = [i for i in range(n) if st[i * 7 + 6] and st[i * 7] == j]
        if not mine:
            continue
        peak = max(sum(AR[i] for i in mine
                       if st[i * 7 + 4] <= t < st[i * 7 + 5])
                   for t in range(H))
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
        if stt not in (cp.OPTIMAL, cp.FEASIBLE):
            print(f"   bay {j}: reschedule {sv.StatusName(stt)} -- keeping ours")
            for i in mine:
                sched[i] = st[i * 7 + 4]
                predicted_z1 += max(0, st[i * 7 + 5] - D[i])
            continue
        for i in mine:
            sched[i] = sv.Value(sv_[i])
            predicted_z1 += max(0, sched[i] + P[i] - D[i])
    pred_obj = base[3] - (base[0] - predicted_z1) * W["w1"]
    print(f"   reschedule: Z1 {base[0]:.0f} -> {predicted_z1}"
          f"   predicted objective {pred_obj:,.0f}")

    # ---- rebuild from empty, day order, forced bay + capped entry + retime
    eng.set_solution([0] * (n * 7))
    eng.build_occ(hz)
    order = sorted(sched, key=lambda i: (sched[i], -P[i]))
    exact = early = inbay = free = lost = 0
    for i in order:
        d, j = sched[i], st[i * 7]
        if eng.insert_capped(i, d, j):
            b = eng.get_block(i)
            if b[4] < d and not eng.retime_block(i, d - b[4], d - b[4]):
                early += 1          # stayed early: unbudgeted occupancy
            else:
                exact += 1
        elif eng.insert_capped(i, -1, j):
            inbay += 1
        elif eng.insert_capped(i, -1, -1):
            free += 1
        else:
            lost += 1
    o = eng.objective()
    sol2 = ma._recs_to_sol(ma._extract(eng, n), eng.entry_ranks())
    res = ma._check(prob, sol2)
    feas = res.get("feasible") if res else None
    print(f"   realized: {o[3]:>12,.0f}  Z1={o[0]:.0f} Z2={o[1]:.0f} Z3={o[2]:.0f}"
          f"   checker feasible={feas}")
    print(f"   locks: {exact} on schedule, {early} early, {inbay} late-in-bay,"
          f" {free} anywhere, {lost} unplaced")
    print(f"   vs board {(o[3]-base[3])/base[3]*100:+.2f}%"
          f"   vs prediction {(o[3]-pred_obj)/pred_obj*100:+.2f}%")


if __name__ == "__main__":
    main()
