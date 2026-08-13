#!/usr/bin/env python3
"""Improve the assignment while a ZERO-TARDINESS packing still exists.

The numbers say P3's gap is an assignment gap and that it is reachable:

    assignment lower bound   36,759      (CP-SAT, proven OPTIMAL, ignores packing)
    all-time target          64,695
    ours                     78,705

The target sits BETWEEN the bound and us, so somebody found an assignment far
better than ours that still packs with z1 = 0. asgmove failed to get there
because it moved the blocks CP-SAT ranked best, and that ranking ignores
packing -- every move it made created tardiness, which costs 89x more per unit
than the regret it saved.

So let feasibility choose the moves: hill-climb the assignment cost
(w2*spread + w3*regret) and accept a move only if tardiness does not rise.

The first version of this probe used reconstructAssigned(tight=True), whose
comment says it "asks whether this assignment admits a zero-tardiness packing".
It does not -- it tries ONE least-slack-first order, and it rejected P3's own
shipped assignment even though that board has z1 = 0 and is therefore itself
such a packing (instrument defect 9). The test here instead asks the question
the way the solver would: lock the assignment, rebuild, anneal a few seconds,
read z1. It is verified against the shipped assignment before any candidate is
judged, and the probe refuses to run if that verification fails.

Reports the annealed result too, since a cheaper assignment the anneal cannot
exploit is not worth shipping.

    python3 tools/asgclimb.py prob_3 240 120 900
"""
import json
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
from board import get_board


def main():
    pid = sys.argv[1]
    tl = float(sys.argv[2])
    T = float(sys.argv[3]) if len(sys.argv) > 3 else 120.0
    LIM = float(sys.argv[4]) if len(sys.argv) > 4 else 420.0
    base, D, P, eng, hz, prob = get_board(pid, tl, 7)
    sys.path.insert(0, str(REPO / "solver"))
    import myalgorithm as ma
    B, bays, W = prob["blocks"], prob["bays"], prob["weights"]
    n, m = len(B), len(bays)
    areas = [b["width"] * b["height"] for b in bays]
    u = [sum(areas) / m / a for a in areas]
    wl = [b["workload"] for b in B]
    PREF = [[float(v) for v in b["bay_preferences"]] for b in B]
    SMAX = [max(p) for p in PREF]

    def cost(a):
        load = [0.0] * m
        reg = 0.0
        for i in range(n):
            load[a[i]] += u[a[i]] * wl[i]
            reg += SMAX[i] - PREF[i][a[i]]
        return W["w2"] * (max(load) - min(load)) + W["w3"] * reg

    cur = [base[i * 7] for i in range(n)]
    eng.set_solution(base)
    eng.build_occ(hz)
    z1, z2, z3, tot = eng.objective()
    print(f"{pid}: shipped {tot:,.0f}  z1 {z1:,.0f}  asg-cost {cost(cur):,.0f}",
          flush=True)

    # FEASIBILITY TEST. reconstructAssigned(tight=True) is NOT an oracle: it
    # tries one least-slack-first order, and it rejected P3's own shipped
    # assignment even though that board has z1 = 0 and is itself a
    # zero-tardiness packing (instrument defect 9). So test the way the solver
    # would actually use the assignment -- lock it, rebuild, give the anneal a
    # few seconds, and ask whether z1 came out at 0.
    PROBE = 4.0

    def z1_of(a):
        eng.set_assignment_lock(list(a))
        if eng.reconstruct_assigned(list(a), False, 7) != -1:
            eng.set_assignment_lock([])
            return None
        eng.build_occ(hz)
        eng.run_portfolio(ma._THR, PROBE, 1, 0.05, 0.15, 0.002, 0.9995,
                          31337, 1, [eng.get_solution()])
        o = eng.objective()
        eng.set_assignment_lock([])
        return o[0]

    base_z1 = z1_of(cur)
    print(f"   shipped assignment under a {PROBE:.0f}s probe: z1={base_z1}",
          flush=True)
    if base_z1 is None or base_z1 > 0:
        print("   the test cannot even reproduce z1=0 on the shipped"
              " assignment -- it would reject every candidate unfairly",
              flush=True)
        return

    t0 = time.time()
    c0 = cost(cur)
    acc = rej = 0
    # candidates: every block not on its favourite bay, best-gain first
    while time.time() - t0 < LIM:
        cand = []
        cc = cost(cur)
        for i in range(n):
            if PREF[i][cur[i]] == SMAX[i]:
                continue
            for j in range(m):
                if j == cur[i] or PREF[i][j] <= PREF[i][cur[i]]:
                    continue
                a = list(cur)
                a[i] = j
                g = cost(a) - cc
                if g < 0:
                    cand.append((g, i, j))
        if not cand:
            break
        cand.sort()
        moved = False
        for g, i, j in cand[:8]:
            if time.time() - t0 > LIM:
                break
            a = list(cur)
            a[i] = j
            z = z1_of(a)
            if z is not None and z <= base_z1:
                cur = a
                acc += 1
                moved = True
                break
            rej += 1
        if not moved:
            break
    print(f"   climbed: {acc} moves accepted, {rej} rejected by the test,"
          f" asg-cost {c0:,.0f} -> {cost(cur):,.0f}"
          f"  [{time.time()-t0:.0f}s]", flush=True)
    if acc == 0:
        print("   => every improving move raises z1 -- the assignment is"
              " pinned by tardiness, not by preference",
              flush=True)
        return
    eng.set_assignment_lock(list(cur))
    if eng.reconstruct_assigned(list(cur), False, 7) != -1:
        print("   final assignment not packable at all", flush=True)
        eng.set_assignment_lock([])
        return
    eng.build_occ(hz)
    eng.run_portfolio(ma._THR, T, 1, 0.05, 0.15, 0.002, 0.9995, 4242, 1,
                      [eng.get_solution()])
    o = eng.objective()
    # Does the realised board actually SIT on the assignment we climbed? The
    # objective is a function of (bay, entry), so if the climbed cost is not
    # the realised cost, the lock did not hold and the climb optimised a proxy
    # the solver never adopts.
    got = [eng.get_block(i)[0] for i in range(n)]
    pl = [bool(eng.get_block(i)[6]) for i in range(n)]
    onplan = sum(1 for i in range(n) if pl[i] and got[i] == cur[i])
    print(f"   realised board sits on the climbed assignment for"
          f" {onplan}/{n} blocks; realised asg-cost {cost(got):,.0f}"
          f" vs climbed {cost(cur):,.0f}", flush=True)
    eng.set_assignment_lock([])
    res = ma._check(prob, ma._recs_to_sol(ma._extract(eng, n),
                                          eng.entry_ranks()))
    print(f"   annealed {T:.0f}s under the climbed lock: {o[3]:,.0f}"
          f"  (z1 {o[0]:,.0f})  checker="
          f"{res.get('feasible') if res else None}"
          f"   vs shipped {tot:,.0f}: {(o[3]-tot)/tot*100:+.2f}%", flush=True)


if __name__ == "__main__":
    main()
