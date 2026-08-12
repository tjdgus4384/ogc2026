#!/usr/bin/env python3
"""Pure scheduling headroom, with zero density assumptions.

The full-freedom master (master.py) does not converge on the Z1 boards inside
240s (P4: incumbent 5.06M against a bound of 123k), so cut the question down to
its cleanest core. Fix EVERYTHING our shipped board chose -- bay assignment,
orientations, hence Z2 and Z3 -- and re-schedule only the entry days, bay by
bay. Each bay is then an independent one-dimensional cumulative problem over
40-80 intervals, which CP-SAT solves exactly in seconds.

Capacity is the density that bay ACTUALLY reaches on our own board (its
realized peak area), so our own schedule is a feasible point by construction
and the model assumes nothing about packability we have not already
demonstrated. The delta

    Z1(ours)  -  Z1(optimal reschedule at our own density)

is therefore the pure scheduling headroom: how many tardy days exist only
because insertGreedy hands every block its own earliest feasible day instead of
coordinating who yields. w1 times that delta, against the gap to target, is the
verdict on the whole scheduling axis:

    headroom ~ gap      the sacrifice schedule is the gap; build the master
                        and its locked realization;
    headroom ~ 0        even perfect coordination at our density saves nothing,
                        and the axis left is geometric density alone.

A sweep above the realized peak (rho column) shows how the headroom grows if
the packer could be pushed denser.

    python3 tools/master2.py SOLVERDIR prob_4 TL [seed]
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
    sys.path.insert(0, str(REPO / solver_dir))
    os.environ["OGC_SEED"] = str(seed)
    import myalgorithm as ma
    from ortools.sat.python import cp_model as cp
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    B, bays, W = prob["blocks"], prob["bays"], prob["weights"]
    n, m = len(B), len(bays)
    A = [b["width"] * b["height"] for b in bays]

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
    o = eng.objective()
    R = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]
    AR = [int(round(max(poly_area(l) for l in B[i]["shape"][st[i * 7 + 1]]["layers"])))
          for i in range(n)]
    H = max(D) + 64
    print(f"{prob_id}: board {o[3]:,.0f}  Z1={o[0]:.0f}"
          f"   (TL {tl:.0f}s, seed {seed}, w1={W['w1']:,})")

    def resched(cap_mul):
        """optimal per-bay entry reschedule; capacity = cap_mul x realized peak."""
        z1 = 0
        detail = []
        for j in range(m):
            mine = [i for i in range(n) if st[i * 7 + 6] and st[i * 7] == j]
            if not mine:
                continue
            peak = max(sum(AR[i] for i in mine
                           if st[i * 7 + 4] <= t < st[i * 7 + 5])
                       for t in range(H))
            cap = max(1, int(round(cap_mul * peak)))
            ours = sum(max(0, st[i * 7 + 5] - D[i]) for i in mine)
            mdl = cp.CpModel()
            sv_, ev_, tv_, iv = [], [], [], []
            for i in mine:
                s_ = mdl.NewIntVar(R[i], H - P[i], f"s{i}")
                e_ = mdl.NewIntVar(R[i], H, f"e{i}")
                t_ = mdl.NewIntVar(0, H, f"t{i}")
                mdl.Add(e_ == s_ + P[i])
                mdl.Add(t_ >= e_ - D[i])
                iv.append(mdl.NewIntervalVar(s_, P[i], e_, f"iv{i}"))
                sv_.append(s_); ev_.append(e_); tv_.append(t_)
            mdl.AddCumulative(iv, [AR[i] for i in mine], cap)
            mdl.Minimize(sum(tv_))
            sv = cp.CpSolver()
            sv.parameters.max_time_in_seconds = 60.0
            sv.parameters.num_search_workers = 8
            stt = sv.Solve(mdl)
            got = int(sv.ObjectiveValue()) if stt in (cp.OPTIMAL, cp.FEASIBLE) else -1
            opt = "OPT" if stt == cp.OPTIMAL else sv.StatusName(stt)[:4]
            z1 += max(0, got)
            detail.append(f"bay{j} {ours}->{got}({opt},peak {peak}/{A[j]}"
                          f"={peak/A[j]*100:.0f}%)")
        return z1, detail

    for mul, tag in ((1.00, "our own realized density"),
                     (1.10, "+10% denser"),
                     (1.25, "+25% denser")):
        t0 = time.time()
        z1, detail = resched(mul)
        save = (o[0] - z1) * W["w1"]
        print(f"   x{mul:.2f} ({tag}): Z1 {o[0]:.0f} -> {z1}"
              f"   saves {save:,.0f} on the objective   [{time.time()-t0:.0f}s]")
        print(f"        {'  '.join(detail)}")


if __name__ == "__main__":
    main()
