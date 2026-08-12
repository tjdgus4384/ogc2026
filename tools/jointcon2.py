#!/usr/bin/env python3
"""jointcon with two-day lookahead: is the ~28.2M plateau start-independent?

Where the theory stands. The start-hedge family is closed at both TL ends
(48 gate pairs): a better start cannot pay while its polished ceiling equals
the incumbent's, and both greedy-family starts and myopic jointcon starts
polish to the same 28.2-28.9M plateau on P6 (target 25.45M). The winners are
BELOW our plateau, so their configurations are off it entirely. The one
remaining theory is a constructor that lands on a DIFFERENT plateau -- and
the cheapest structural change with that potential is lookahead: the myopic
day CP seats today's cohort with no regard for tomorrow's, which is exactly
the fragmenting choice a two-day joint model avoids.

Same machinery, one window change: each step solves days t and t+1 together
(pair_conflicts already handles cross-day overlap), commits only day t, and
slides. The objective carries jc2's terms -- urgency, w3-scaled preference
regret, bottom-left compactness.

Measured in two stages, decisive number second:
  seed        constructed board vs jc1's 37.1-38.9M and the 43.2M greedy seed
  ceiling     bare 400s portfolio from it, against the 28.2-28.9M plateau.
              BELOW 28.2M breaks the plateau and reopens everything;
              equal closes start engineering on P6 with a third data point.

    python3 tools/jointcon2.py prob_6 [seed] [anneal_sec]
"""
import json
import math
import os
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
SOLVER = "builds/pc2"


def main():
    prob_id = sys.argv[1]
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 7
    anneal = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
    sys.path.insert(0, str(REPO / SOLVER))
    os.environ["OGC_SEED"] = str(seed)
    import myalgorithm as ma
    from ortools.sat.python import cp_model as cp
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    B = prob["blocks"]
    n = len(B)
    w3 = prob["weights"]["w3"]
    R = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]

    eng, D, P = ma._fresh(prob, 4)
    hz = eng.horizon()
    eng.set_solution([0] * (n * 7))
    eng.build_occ(hz)
    t0 = time.time()
    unplaced = set(range(n))
    H = max(D) + 64
    day = 0
    while unplaced and day <= H:
        want = [i for i in unplaced if R[i] <= day + 1]
        if not want:
            day += 1
            continue
        # candidates over the two-day window; feasible_positions gives the
        # earliest feasible day per bay >= eMin, so ask at both days.
        CAND, flat, idx = {}, [], []
        for i in want:
            cs = []
            for d0 in (day, day + 1):
                if R[i] > d0:
                    continue
                raw = eng.feasible_positions(i, 6, d0)
                got = [tuple(raw[k:k + 5]) for k in range(0, len(raw), 5)]
                cs += [c for c in got if c[4] == d0 and c not in cs]
            if cs:
                CAND[i] = cs
                for k, c in enumerate(cs):
                    flat += [i, c[0], c[1], c[2], c[3], c[4]]
                    idx.append((i, k))
        if not CAND:
            day += 1
            continue
        mdl = cp.CpModel()
        V, use = {}, {}
        for i in CAND:
            V[i] = [mdl.NewBoolVar(f"z{i}_{k}") for k in range(len(CAND[i]))]
            use[i] = mdl.NewBoolVar(f"u{i}")
            mdl.Add(sum(V[i]) == use[i])
        prs = eng.pair_conflicts(flat)
        for q in range(0, len(prs), 2):
            (a1, k1), (a2, k2) = idx[prs[q]], idx[prs[q + 1]]
            mdl.AddBoolOr([V[a1][k1].Not(), V[a2][k2].Not()])
        terms = []
        for i in CAND:
            pr = B[i]["bay_preferences"]
            smax = max(pr)
            for k, c in enumerate(CAND[i]):
                slack = (D[i] - P[i]) - c[4]
                urg = 3000 if slack <= 0 else max(1, 3000 // (1 + slack))
                reg = int(round(smax - pr[c[0]]))
                corner = c[3] * 4 + c[2]
                # COMMITMENT PRESSURE -- the v0 failure mode was deferral:
                # tomorrow's value equalled today's, so the CP parked blocks
                # at t+1 forever (Z1 ~9000, and the ballooning waiting set
                # made every day's model huge, 731-810s). A 10% discount on
                # tomorrow breaks every tie toward today while still allowing
                # a genuine reservation when the joint value is higher.
                scale = 100 if c[4] == day else 90
                terms.append((urg * scale - w3 * reg - corner) * V[i][k])
        mdl.Maximize(sum(terms))
        sv = cp.CpSolver()
        sv.parameters.max_time_in_seconds = 1.0
        sv.parameters.num_search_workers = 4
        if sv.Solve(mdl) in (2, 4):
            for i in list(CAND):
                if sv.Value(use[i]):
                    for k, c in enumerate(CAND[i]):
                        if sv.Value(V[i][k]):
                            if c[4] == day:          # commit today only
                                if eng.place_at(i, c[0], c[1], c[2],
                                                c[3], c[4]):
                                    unplaced.discard(i)
                            break
        day += 1
    for i in sorted(unplaced, key=lambda k: -P[k]):
        if eng.insert_capped(i, -1, -1):
            unplaced.discard(i)
    o = eng.objective()
    print(f"{prob_id} seed {seed}: jointcon-2day {o[3]:,.0f}"
          f"  Z1={o[0]:.0f} Z2={o[1]:.0f} Z3={o[2]:.0f}"
          f"  unplaced {len(unplaced)}   [{time.time()-t0:.0f}s]")
    if anneal > 0 and not unplaced:
        jb = eng.get_solution()
        eng.run_portfolio(4, anneal, 1, 0.05, 0.15, 0.002, 0.9995, seed, 1, [jb])
        o2 = eng.objective()
        print(f"   + bare {anneal:.0f}s portfolio -> {o2[3]:,.0f}  Z1={o2[0]:.0f}"
              f"   (plateau reference 28.2-28.9M)")


if __name__ == "__main__":
    main()
