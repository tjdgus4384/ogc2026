#!/usr/bin/env python3
"""The assignment optimum that geometry actually permits.

asgbound dropped geometry entirely and proved P3's optimum at 36,759 (Z2=6,632,
Z3=24). asgprobe then locked that assignment and the kernel came back at
94,890-99,340 with Z3=524-593 -- worse than running unlocked. The Z2<=3262
assignment (Z3=253) fared no better. So neither is reachable, and the reason is
the one z2move recorded long ago: the preferred bays are full precisely because
everyone prefers them.

That means asgbound's bound is loose in a way that matters, and the missing
constraint is capacity. This adds it, using the shipped board's own entry times
-- a real, punctual, feasible schedule -- so the model is anchored to something
the packer demonstrably achieves:

    for every day t and bay j:
        sum of footprint area over blocks assigned to j and resident at t
            <=  rho * area(j)

rho is the density our packer actually reaches, not a theoretical one: on P3 the
shipped board peaks at 59% / 39% / 37% per bay, so 0.60 is generous to the model
rather than to us.

The answer settles what P3's remaining 21.7% is made of:

    capacity-feasible Z3 near ours (~414)   assignment is already near its
                                            reachable optimum, and the gap is
                                            somewhere else entirely;
    capacity-feasible Z3 near 300           the room is real and a better
                                            assignment rule is worth building.

    python3 tools/asgcap.py SOLVERDIR prob_3 TL [rho] [seed]
"""
import json
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
SC = 1000


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
    rho = float(sys.argv[4]) if len(sys.argv) > 4 else 0.60
    seed = int(sys.argv[5]) if len(sys.argv) > 5 else 7
    sys.path.insert(0, str(REPO / solver_dir))
    os.environ["OGC_SEED"] = str(seed)
    import myalgorithm as ma
    from ortools.sat.python import cp_model as cp
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    B, bays, W = prob["blocks"], prob["bays"], prob["weights"]
    n, m = len(B), len(bays)
    areas = [b["width"] * b["height"] for b in bays]
    u = [sum(areas) / m / a for a in areas]
    wl = [b["workload"] for b in B]
    PREF = [[float(v) for v in b["bay_preferences"]] for b in B]
    REG = [[int(round(max(PREF[i]) - PREF[i][j])) for j in range(m)]
           for i in range(n)]
    uw = [int(round(SC * u[j])) for j in range(m)]

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
    print(f"{prob_id}: shipped {o[3]:,.0f}  Z1={o[0]:.0f} Z2={o[1]:.0f} Z3={o[2]:.0f}"
          f"   (rho={rho}, TL {tl:.0f}s, seed {seed})")

    # footprint at the orientation the board actually used, and the board's
    # own entry times: the schedule is fixed, only the bay is decided here.
    AR = [max(poly_area(l) for l in B[i]["shape"][st[i * 7 + 1]]["layers"])
          for i in range(n)]
    E = [st[i * 7 + 4] for i in range(n)]
    F = [st[i * 7 + 5] for i in range(n)]
    days = sorted({E[i] for i in range(n)} | {F[i] for i in range(n)})

    mdl = cp.CpModel()
    x = [[mdl.NewBoolVar(f"x{i}_{j}") for j in range(m)] for i in range(n)]
    for i in range(n):
        mdl.AddExactlyOne(x[i])
    for t in days:
        live = [i for i in range(n) if E[i] <= t < F[i]]
        if not live:
            continue
        for j in range(m):
            mdl.Add(sum(int(round(AR[i])) * x[i][j] for i in live)
                    <= int(rho * areas[j]))
    V = []
    tot = sum(wl)
    for j in range(m):
        v = mdl.NewIntVar(0, uw[j] * tot, f"v{j}")
        mdl.Add(v == sum(uw[j] * wl[i] * x[i][j] for i in range(n)))
        V.append(v)
    hi = mdl.NewIntVar(0, max(uw) * tot, "hi")
    lo = mdl.NewIntVar(0, max(uw) * tot, "lo")
    mdl.AddMaxEquality(hi, V)
    mdl.AddMinEquality(lo, V)
    spread = mdl.NewIntVar(0, max(uw) * tot, "sp")
    mdl.Add(spread == hi - lo)
    z3 = mdl.NewIntVar(0, sum(max(r) for r in REG), "z3")
    mdl.Add(z3 == sum(REG[i][j] * x[i][j] for i in range(n) for j in range(m)))
    mdl.Minimize(W["w2"] * spread + W["w3"] * SC * z3)
    sv = cp.CpSolver()
    sv.parameters.max_time_in_seconds = 180.0
    sv.parameters.num_search_workers = 8
    stt = sv.Solve(mdl)
    name = sv.StatusName(stt)
    if stt not in (cp.OPTIMAL, cp.FEASIBLE):
        print(f"   capacity-constrained assignment: {name}"
              f"   (rho={rho} admits nothing -- the schedule itself needs more)")
        return
    z2v, z3v = sv.Value(spread) / SC, sv.Value(z3)
    obj = sv.ObjectiveValue() / SC
    moved = sum(1 for i in range(n)
                if not sv.Value(x[i][st[i * 7]]))
    print(f"   capacity-feasible optimum: {obj:,.0f}   ({name})")
    print(f"      Z2={z2v:,.0f} Z3={z3v:.0f}   vs shipped Z2={o[1]:.0f} Z3={o[2]:.0f}"
          f"   ({moved} of {n} blocks would change bay)")
    print(f"      w1 = {W['w1']:,}: one tardy day costs "
          f"{W['w1']/max(1.0, o[3]-obj)*100:.0f}% of this gain")

    # ---- and can our own board actually take it?
    # The blocks that differ have to move TOGETHER: z2move showed single
    # preference relocations land punctually 1 time in 19, because the bay a
    # block wants is full of blocks that also want it. Freeing all of them at
    # once and re-inserting each into its CP bay is a different move, and it is
    # the only one that can realise this assignment.
    diff = [i for i in range(n) if not sv.Value(x[i][st[i * 7]])]
    tgt = [next(j for j in range(m) if sv.Value(x[i][j])) for i in diff]
    part = list(st)
    for i in diff:
        part[i * 7 + 6] = 0
    eng.set_solution(part)
    eng.build_occ(eng.horizon())
    lock = [-1] * n
    for i, j in zip(diff, tgt):
        lock[i] = j
    eng.set_assignment_lock(lock)
    ok, late = 0, 0
    for i in sorted(diff, key=lambda k: -P[k]):
        cap = max(int(B[i]["release_time"]), D[i] - P[i])
        if eng.insert_capped(i, cap, lock[i]):
            ok += 1
            if eng.get_block(i)[5] > D[i]:
                late += 1
    eng.set_assignment_lock([])
    o2 = eng.objective()
    print(f"      realising it on our own board: {ok}/{len(diff)} re-inserted"
          f" into their CP bay, {late} of those tardy")
    if ok == len(diff):
        print(f"      -> board {o2[3]:,.0f}  Z1={o2[0]:.0f} Z2={o2[1]:.0f}"
              f" Z3={o2[2]:.0f}   ({(o2[3]-o[3])/o[3]*100:+.2f}%)")
    else:
        print(f"      -> NOT realisable: {len(diff)-ok} could not be placed in "
              f"their CP bay at all")


if __name__ == "__main__":
    main()
