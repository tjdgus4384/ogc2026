#!/usr/bin/env python3
"""An exact per-bay realizer -- and its own validation comes first.

control_rebuild settled the instrument question: the day-order greedy rebuild
cannot even reconstruct OUR OWN board's schedule (Z1 169 -> 605, 77 of 150
late), so every realization verdict built on it was an artifact. The
scheduling axis is open again, and the only question is a good realizer.

With the schedule fixed, bays are independent, so an exact realizer is a
bay-sized problem: every block gets a candidate set = (orientation x integer
grid anchor) at its SCHEDULED entry day, pairwise compatibility comes from the
kernel's pair_conflicts -- the pc2 build, whose same-day tie model is
deterministically correct and is load-bearing here, since a schedule packs
same-day pairs in bulk -- and a CP-SAT feasibility solve picks one candidate
per block or proves none exists.

The instrument is validated before it judges anything:

    mode=board    realize the bay's OWN schedule (witness anchors included in
                  the candidate set). SAT is guaranteed to exist, so anything
                  other than SAT is an encoding bug, not a finding.
    mode=master   realize the count-capped reschedule (entries per (bay, day)
                  capped at what this bay itself demonstrates). SAT here is
                  the first TRUE positive verdict on the scheduling axis;
                  UNSAT (at stride 1) is the first true negative.

    python3 tools/exact_realizer.py prob_4 BAY board|master TL [stride] [seed]
"""
import json
import math
import os
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
SOLVER = "builds/pc2"      # correct same-day pair model; see docstring


def poly_area(v):
    s = 0.0
    for k in range(len(v)):
        x1, y1 = v[k]
        x2, y2 = v[(k + 1) % len(v)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2


def main():
    prob_id = sys.argv[1]
    bay = int(sys.argv[2])
    mode = sys.argv[3]
    tl = float(sys.argv[4])
    stride = int(sys.argv[5]) if len(sys.argv) > 5 else 2
    seed = int(sys.argv[6]) if len(sys.argv) > 6 else 7
    sys.path.insert(0, str(REPO / SOLVER))
    os.environ["OGC_SEED"] = str(seed)
    import myalgorithm as ma
    from ortools.sat.python import cp_model as cp
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    B, bays = prob["blocks"], prob["bays"]
    n, m = len(B), len(bays)
    W_, H_ = bays[bay]["width"], bays[bay]["height"]

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
    mine = [i for i in range(n) if st[i * 7 + 6] and st[i * 7] == bay]
    ours_z1 = sum(max(0, st[i * 7 + 5] - D[i]) for i in mine)
    R = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]
    AR = [int(round(max(poly_area(l) for l in B[i]["shape"][st[i * 7 + 1]]["layers"])))
          for i in range(n)]
    H = max(D) + 64
    print(f"{prob_id} bay {bay} [{mode}]: board {base[3]:,.0f}, {len(mine)} blocks"
          f" here, our bay Z1={ours_z1}   (TL {tl:.0f}s, stride {stride})")

    # ---- the schedule to realize
    if mode == "board":
        sched = {i: st[i * 7 + 4] for i in mine}
        pred = ours_z1
    else:
        peak = max(sum(AR[i] for i in mine
                       if st[i * 7 + 4] <= t < st[i * 7 + 5]) for t in range(H))
        ccap = max(sum(1 for i in mine if st[i * 7 + 4] == t) for t in range(H))
        mdl = cp.CpModel()
        sv_, tv_, iv, pu = {}, [], [], []
        for i in mine:
            s_ = mdl.NewIntVar(R[i], H - P[i], f"s{i}")
            e_ = mdl.NewIntVar(R[i], H, f"e{i}")
            t_ = mdl.NewIntVar(0, H, f"t{i}")
            mdl.Add(e_ == s_ + P[i])
            mdl.Add(t_ >= e_ - D[i])
            iv.append(mdl.NewIntervalVar(s_, P[i], e_, f"iv{i}"))
            pu.append(mdl.NewFixedSizeIntervalVar(s_, 1, f"pu{i}"))
            sv_[i] = s_
            tv_.append(t_)
        mdl.AddCumulative(iv, [AR[i] for i in mine], peak)
        mdl.AddCumulative(pu, [1] * len(mine), max(1, ccap))
        mdl.Minimize(sum(tv_))
        sv = cp.CpSolver()
        sv.parameters.max_time_in_seconds = 120.0
        sv.parameters.num_search_workers = 8
        stt = sv.Solve(mdl)
        if stt not in (cp.OPTIMAL, cp.FEASIBLE):
            print("   reschedule failed; abort")
            return
        sched = {i: sv.Value(sv_[i]) for i in mine}
        pred = sum(max(0, sched[i] + P[i] - D[i]) for i in mine)
        print(f"   count-capped reschedule (peak {peak}, <= {ccap} entries/day):"
              f" bay Z1 {ours_z1} -> {pred}")

    # ---- candidate sets: every orientation x integer anchor inside the bay
    t0 = time.time()
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
            for x in range(xlo, xhi + 1, stride):
                for y in range(ylo, yhi + 1, stride):
                    cs.append((bay, oi, x, y, sched[i]))
        if mode == "board":
            w = (bay, st[i * 7 + 1], st[i * 7 + 2], st[i * 7 + 3], sched[i])
            if w not in cs:
                cs.append(w)                    # the witness anchor
        CAND[i] = cs
        for k, c in enumerate(cs):
            flat += [i, c[0], c[1], c[2], c[3], c[4]]
            idx.append((i, k))
    ncand = len(idx)
    print(f"   candidates: {ncand:,} ({ncand / max(1, len(mine)):.0f}/block)"
          f"   [{time.time()-t0:.0f}s]")
    t1 = time.time()
    prs = eng.pair_conflicts(flat)
    print(f"   {len(prs)//2:,} conflict pairs   [{time.time()-t1:.0f}s]")

    mdl = cp.CpModel()
    V = {i: [mdl.NewBoolVar(f"z{i}_{k}") for k in range(len(CAND[i]))]
         for i in mine}
    for i in mine:
        mdl.AddExactlyOne(V[i])
    for q in range(0, len(prs), 2):
        (a1, k1), (a2, k2) = idx[prs[q]], idx[prs[q + 1]]
        mdl.AddBoolOr([V[a1][k1].Not(), V[a2][k2].Not()])
    sv = cp.CpSolver()
    sv.parameters.max_time_in_seconds = 300.0
    sv.parameters.num_search_workers = 8
    t2 = time.time()
    stt = sv.Solve(mdl)
    nm = sv.StatusName(stt)
    if stt not in (cp.OPTIMAL, cp.FEASIBLE):
        print(f"   realizer: {nm}   [{time.time()-t2:.0f}s]"
              + ("   <- ENCODING BUG (witness existed)" if mode == "board"
                 else "   <- schedule truly infeasible at this stride"))
        return
    # commit on an otherwise-empty yard slice and verify with the kernel's vp
    part = list(st)
    for i in mine:
        part[i * 7 + 6] = 0
    eng.set_solution(part)
    eng.build_occ(eng.horizon())
    bad = 0
    for i in mine:
        for k, c in enumerate(CAND[i]):
            if sv.Value(V[i][k]):
                if not eng.place_at(i, c[0], c[1], c[2], c[3], c[4]):
                    bad += 1
                break
    o = eng.objective()
    print(f"   realizer: {nm} in {time.time()-t2:.0f}s -> bay Z1 {pred} REALIZED"
          f"   (place_at rejects: {bad}, whole board {o[3]:,.0f},"
          f" {(o[3]-base[3])/base[3]*100:+.2f}%)")


if __name__ == "__main__":
    main()
