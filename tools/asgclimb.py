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

The fix is to let feasibility choose the moves. reconstructAssigned's own
comment names the oracle: "tight=true asks whether this assignment admits a
zero-tardiness packing". So hill-climb the assignment cost (w2*spread +
w3*regret) and accept a move ONLY if the tight reconstruction still succeeds.
Every accepted assignment is therefore packable with z1 = 0 by construction,
and the objective can only fall.

Reports the annealed result too, since a cheaper assignment that the anneal
cannot exploit is not worth shipping.

    python3 tools/asgclimb.py prob_3 240 120
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

    def tight_ok(a):
        return eng.reconstruct_assigned(list(a), True, 7) == -1

    if not tight_ok(cur):
        print("   the shipped assignment does not admit a zero-tardiness"
              " packing -- the oracle cannot steer here", flush=True)
        return
    print("   shipped assignment is tight-feasible; climbing", flush=True)

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
        for g, i, j in cand[:40]:
            if time.time() - t0 > LIM:
                break
            a = list(cur)
            a[i] = j
            if tight_ok(a):
                cur = a
                acc += 1
                moved = True
                break
            rej += 1
        if not moved:
            break
    print(f"   climbed: {acc} moves accepted, {rej} rejected by the oracle,"
          f" asg-cost {c0:,.0f} -> {cost(cur):,.0f}"
          f"  [{time.time()-t0:.0f}s]", flush=True)
    if acc == 0:
        print("   => every improving move breaks zero-tardiness packing",
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
    eng.set_assignment_lock([])
    res = ma._check(prob, ma._recs_to_sol(ma._extract(eng, n),
                                          eng.entry_ranks()))
    print(f"   annealed {T:.0f}s under the climbed lock: {o[3]:,.0f}"
          f"  (z1 {o[0]:,.0f})  checker="
          f"{res.get('feasible') if res else None}"
          f"   vs shipped {tot:,.0f}: {(o[3]-tot)/tot*100:+.2f}%", flush=True)


if __name__ == "__main__":
    main()
