#!/usr/bin/env python3
"""jointcon v0: construct day by day, seating each day's cohort JOINTLY.

Where the elimination landed. Every decomposed axis is measured rigid: per-bay
packing is optimal over the vocabulary at our schedule (bayscratch), no extra
block fits at our positions (overfill), the board is a compaction fixpoint,
restarts sample a normal tail, and rescheduling our own packings realizes
nothing (headroom, on a validated instrument). What separates the winners'
boards (P4 Z1 95 vs our 170) must therefore be built into the GEOMETRY at
construction: more blocks entering per day, packed jointly, than sequential
first-fit ever seats.

This is the first constructor that has that lever. Instead of insertGreedy's
one-block-at-a-time earliest-day first-fit corner, it walks the calendar and,
each day, chooses WHICH waiting blocks enter and WHERE, in one CP:

    candidates   the kernel's own feasible_positions at exactly this day
    conflicts    pair_conflicts (builds/pc2 -- the correct same-day tie model,
                 validated to reproduce our own bays exactly)
    objective    urgency-weighted: seating a block with little slack left
                 outweighs seating several with plenty; blocks past their
                 punctual window carry full w1 weight per day saved

Judged in two stages, seed first: the raw constructed board against
_seed_free's best seed (P4 seed Z1 ~ 550 against the pipeline's final 170).
Beating the seed substantially earns stage (b), a full-TL anneal comparison.

    python3 tools/jointcon.py prob_4 [seed]
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
    sys.path.insert(0, str(REPO / SOLVER))
    os.environ["OGC_SEED"] = str(seed)
    import myalgorithm as ma
    from ortools.sat.python import cp_model as cp
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    B = prob["blocks"]
    n = len(B)
    R = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]

    # ---- the yardstick: the kernel's own best-of-5 seed
    t0 = time.time()
    eng0, seeds = ma._seed_free(prob)
    o0 = eng0.objective()
    print(f"{prob_id}: _seed_free seed {o0[3]:,.0f}  Z1={o0[0]:.0f}"
          f"   [{time.time()-t0:.0f}s]")

    # ---- jointcon v0
    eng, D, P = ma._fresh(prob, 4)
    hz = eng.horizon()
    eng.set_solution([0] * (n * 7))
    eng.build_occ(hz)
    t1 = time.time()
    unplaced = set(range(n))
    H = max(D) + 64
    day = 0
    while unplaced and day <= H:
        want = [i for i in unplaced if R[i] <= day]
        if not want:
            day += 1
            continue
        # candidates at exactly this day, any bay
        CAND, flat, idx = {}, [], []
        for i in want:
            raw = eng.feasible_positions(i, 6, day)
            cs = [tuple(raw[k:k + 5]) for k in range(0, len(raw), 5)]
            cs = [c for c in cs if c[4] == day]
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
        # urgency: a block at the edge of its punctual window (or past it)
        # must not wait for a block with weeks of slack. Weight = per-day cost
        # of waiting one more day, +1 so even slack-rich blocks prefer entry.
        wgt = {}
        for i in CAND:
            slack = (D[i] - P[i]) - day
            wgt[i] = 1000 if slack <= 0 else max(1, 1000 // (1 + slack))
        mdl.Maximize(sum(wgt[i] * use[i] for i in CAND))
        sv = cp.CpSolver()
        sv.parameters.max_time_in_seconds = 6.0
        sv.parameters.num_search_workers = 4
        if sv.Solve(mdl) in (cp.OPTIMAL, cp.FEASIBLE):
            for i in list(CAND):
                if sv.Value(use[i]):
                    for k, c in enumerate(CAND[i]):
                        if sv.Value(V[i][k]):
                            if eng.place_at(i, c[0], c[1], c[2], c[3], c[4]):
                                unplaced.discard(i)
                            break
        day += 1
    o = eng.objective()
    left = len(unplaced)
    sol2 = ma._recs_to_sol(ma._extract(eng, n), eng.entry_ranks())
    res = ma._check(prob, sol2)
    feas = res.get("feasible") if res else None
    print(f"   jointcon v0: {o[3]:,.0f}  Z1={o[0]:.0f} Z2={o[1]:.0f} Z3={o[2]:.0f}"
          f"   unplaced {left}   checker feasible={feas}   [{time.time()-t1:.0f}s]")
    print(f"   vs seed: {(o[3]-o0[3])/o0[3]*100:+.2f}%"
          + ("   <- beats the seed; stage (b) anneal comparison is earned"
             if o[3] < o0[3] and not left else ""))


if __name__ == "__main__":
    main()
