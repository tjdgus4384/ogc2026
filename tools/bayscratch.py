#!/usr/bin/env python3
"""Rebuild ONE BAY from nothing, exactly. Is our packing of it near-optimal?

Every exact experiment so far re-placed a subset AROUND the existing board:
terminal.py and exactrepair.py free 5-60% of the blocks and refill the hole, and
the vocabulary sweep (richvocab) widens the candidate set for the same move.
All of them return zero. But all of them also keep most of the board frozen, so
they can only ever confirm that the incumbent is a local optimum -- which it is,
measured every way we know how.

This asks the different question. Take one bay, free EVERY block our solution
puts there, and rebuild that bay from nothing with CP over a rich candidate set
(many entry days, many positions). Nothing is frozen inside the bay, so the
optimiser is not repairing our board -- it is building its own.

  CP beats us on the bay   the destroy-repair-with-greedy-insertion skeleton is
                           what caps the quality level, and a decomposition
                           solver is worth building;
  CP matches us            our packing of a bay is already near-optimal and the
                           gap lives in the bay ASSIGNMENT, not the packing.

Either answer redirects the work, and neither is knowable from the probes run so
far.

    python3 tools/bayscratch.py SOLVERDIR prob_5 TL BAY [seed]
"""
import json
import os
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent


def main():
    solver_dir, prob_id = sys.argv[1], sys.argv[2]
    tl = float(sys.argv[3])
    bay = int(sys.argv[4])
    seed = int(sys.argv[5]) if len(sys.argv) > 5 else 7
    sys.path.insert(0, str(REPO / solver_dir))
    os.environ["OGC_SEED"] = str(seed)
    import myalgorithm as ma
    from ortools.sat.python import cp_model as cp
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    B = prob["blocks"]
    n = len(B)
    W = prob["weights"]
    PREF = [[float(v) for v in b["bay_preferences"]] for b in B]
    SMAX = [max(pr) for pr in PREF]

    sol = ma.algorithm(prob, timelimit=tl)
    eng, D, P = ma._fresh(prob, 4)
    st = [0] * (n * 7)
    for day, ops in sol["operations"].items():
        for op in ops:
            i = op["block_id"]
            if op["type"] == "ENTRY":
                st[i * 7:i * 7 + 7] = [op["bay_id"], op["orient_idx"], op["x"],
                                       op["y"], int(day), int(day) + P[i], 1]
    hz = eng.horizon()
    eng.set_solution(st)
    eng.build_occ(hz)
    base = eng.objective()

    mine = [i for i in range(n) if st[i * 7 + 6] and st[i * 7] == bay]
    # what OUR board scores on exactly these blocks
    ours = sum(W["w1"] * max(0, st[i * 7 + 5] - D[i])
               + W["w3"] * (SMAX[i] - PREF[i][bay]) for i in mine)
    print(f"{prob_id} bay {bay}: board {base[3]:,.0f}; {len(mine)} blocks live here")
    print(f"   our w1*Z1 + w3*Z3 on those blocks: {ours:,.0f}")

    # free the whole bay, then enumerate a rich candidate set for each block
    part = list(st)
    for i in mine:
        part[i * 7 + 6] = 0
    eng.set_solution(part)
    eng.build_occ(hz)
    t0 = time.time()
    CAND = {}
    for i in mine:
        cs, emin, r = [], 0, 0
        while r < 12 and len(cs) < 40:
            raw = eng.feasible_positions(i, 6, emin)
            if not raw:
                break
            got = [tuple(raw[k:k + 5]) for k in range(0, len(raw), 5)]
            got = [c for c in got if c[0] == bay]      # this bay only
            cs += [c for c in got if c not in cs]
            allg = [tuple(raw[k:k + 5]) for k in range(0, len(raw), 5)]
            emin = min(c[4] for c in allg) + 1
            r += 1
        c0 = tuple(st[i * 7:i * 7 + 5])
        if c0 not in cs:
            cs.append(c0)
        CAND[i] = cs
    ncand = sum(len(v) for v in CAND.values())
    print(f"   candidates: {ncand} ({ncand / max(1, len(mine)):.1f} per block), "
          f"enumerated in {time.time() - t0:.0f}s")

    mdl = cp.CpModel()
    V, flat, idx = {}, [], []
    for i in mine:
        V[i] = [mdl.NewBoolVar(f"z{i}_{k}") for k in range(len(CAND[i]))]
        mdl.AddExactlyOne(V[i])
        for k, c in enumerate(CAND[i]):
            flat += [i, c[0], c[1], c[2], c[3], c[4]]
            idx.append((i, k))
    t1 = time.time()
    prs = eng.pair_conflicts(flat)
    for t in range(0, len(prs), 2):
        (i, k), (i2, k2) = idx[prs[t]], idx[prs[t + 1]]
        mdl.AddBoolOr([V[i][k].Not(), V[i2][k2].Not()])
    print(f"   {len(prs)//2} conflict pairs in {time.time() - t1:.0f}s")
    mdl.Minimize(sum((W["w1"] * max(0, c[4] + P[i] - D[i])
                      + W["w3"] * (SMAX[i] - PREF[i][c[0]])) * V[i][k]
                     for i in mine for k, c in enumerate(CAND[i])))
    sv = cp.CpSolver()
    sv.parameters.max_time_in_seconds = 300.0
    sv.parameters.num_search_workers = 8
    stt = sv.Solve(mdl)
    name = sv.StatusName(stt)
    if stt not in (cp.OPTIMAL, cp.FEASIBLE):
        print(f"   CP: {name}")
        return
    val = sv.ObjectiveValue()
    print(f"   CP {name} in {sv.WallTime():.0f}s: {val:,.0f} "
          f"vs ours {ours:,.0f}  ({(val - ours) / max(1, ours) * 100:+.1f}%)"
          f"   bound {sv.BestObjectiveBound():,.0f}")

    # realise it and check with the engine's own vp
    new = list(part)
    for i in mine:
        for k, c in enumerate(CAND[i]):
            if sv.Value(V[i][k]):
                new[i * 7:i * 7 + 7] = [c[0], c[1], c[2], c[3], c[4],
                                        c[4] + P[i], 1]
                break
    eng.set_solution(new)
    eng.build_occ(hz)
    bad = [i for i in range(n) if new[i * 7 + 6] and not eng.vp_ok(i)]
    o = eng.objective()
    print(f"   realised: vp violations {len(bad)}, whole-board objective "
          f"{o[3]:,.0f} ({(o[3] - base[3]) / base[3] * 100:+.2f}%)")


if __name__ == "__main__":
    main()
