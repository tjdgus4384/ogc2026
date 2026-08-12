#!/usr/bin/env python3
"""Can the yard hold MORE than we put in it?

Every probe this campaign has run asks one of two questions, and neither is the
one that matters:

    "can this board improve?"        terminal, exactrepair, richvocab, z2move,
                                     compact2d -- all zero
    "can these blocks be packed      bayscratch -- CP returns EXACTLY our value
     better?"                        with the proven bound equal to it

The second reads like a wall, and it is not. bayscratch frees a bay and refills
it with THE SAME BLOCKS -- the ones we already chose to put there. The tardy
blocks were never in that set. So "our packing is optimal" carries a silent
qualifier: optimal FOR THE SET WE PICKED. Whether the set could have been
bigger has never been asked.

This asks it. Take the bay and day under the most tardy pressure, take the
residents we actually placed there, ADD the tardy blocks whose punctual window
covers that day, and let CP maximise how many fit simultaneously -- same
geometry, same crane rules, same candidate vocabulary as the kernel's own
repair.

    CP fits more than we did     the packing search leaves placements on the
                                 table, and that is the whole Z1 gap;
    CP fits no more              the yard genuinely cannot hold them and Z1 is
                                 forced -- then the gap is not in packing at all.

    python3 tools/overfill.py SOLVERDIR prob_4 TL [seed]
"""
import collections
import json
import os
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent


def main():
    solver_dir, prob_id = sys.argv[1], sys.argv[2]
    tl = float(sys.argv[3])
    seed = int(sys.argv[4]) if len(sys.argv) > 4 else 7
    sys.path.insert(0, str(REPO / solver_dir))
    os.environ["OGC_SEED"] = str(seed)
    import myalgorithm as ma
    from ortools.sat.python import cp_model as cp
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    B, bays = prob["blocks"], prob["bays"]
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
    tardy = [i for i in range(n) if st[i * 7 + 6] and st[i * 7 + 5] > D[i]]
    print(f"{prob_id}: board {base[3]:,.0f}  Z1={base[0]:.0f}  {len(tardy)} tardy"
          f"   (TL {tl:.0f}s, seed {seed})")

    # the (bay, day) under the most pressure: most tardy blocks whose punctual
    # window still covered that day.
    press = collections.Counter()
    for i in tardy:
        lo = max(int(B[i]["release_time"]), 0)
        hi = D[i] - P[i]
        for t in range(lo, hi + 1):
            for j in range(m):
                press[(j, t)] += 1
    for (j, t), cnt in press.most_common(3):
        res = [i for i in range(n) if st[i * 7 + 6] and st[i * 7] == j
               and st[i * 7 + 4] <= t < st[i * 7 + 5]]
        want = [i for i in tardy
                if max(int(B[i]["release_time"]), 0) <= t <= D[i] - P[i]]
        want = want[:12]
        print(f"   bay {j} day {t}: we place {len(res)}, "
              f"{cnt} tardy blocks could still be punctual here "
              f"(testing {len(res)} + {len(want)})")

        # free everything in that bay at that day, then ask CP to seat
        # residents AND as many of the wanted blocks as possible.
        part = list(st)
        for i in res + want:
            part[i * 7 + 6] = 0
        eng.set_solution(part)
        eng.build_occ(hz)
        CAND, t0 = {}, time.time()
        for i in res + want:
            cs, emin, r = [], 0, 0
            while r < 8 and len(cs) < 40:
                raw = eng.feasible_positions(i, 8, emin)
                if not raw:
                    break
                got = [tuple(raw[k:k + 5]) for k in range(0, len(raw), 5)]
                cs += [c for c in got if c[0] == j and c[4] <= t and c not in cs]
                emin = min(c[4] for c in got) + 1
                r += 1
            if i in res:
                c0 = tuple(st[i * 7:i * 7 + 5])
                if c0 not in cs:
                    cs.append(c0)
            CAND[i] = cs
        mdl = cp.CpModel()
        V, use, flat, idx = {}, {}, [], []
        for i in res + want:
            if not CAND[i]:
                continue
            V[i] = [mdl.NewBoolVar(f"z{i}_{k}") for k in range(len(CAND[i]))]
            use[i] = mdl.NewBoolVar(f"u{i}")
            mdl.Add(sum(V[i]) == use[i])
            if i in res:
                mdl.Add(use[i] == 1)          # never place FEWER than we do
            for k, c in enumerate(CAND[i]):
                flat += [i, c[0], c[1], c[2], c[3], c[4]]
                idx.append((i, k))
        prs = eng.pair_conflicts(flat)
        for q in range(0, len(prs), 2):
            (i1, k1), (i2, k2) = idx[prs[q]], idx[prs[q + 1]]
            mdl.AddBoolOr([V[i1][k1].Not(), V[i2][k2].Not()])
        extra = [use[i] for i in want if i in use]
        mdl.Maximize(sum(extra) if extra else 0)
        sv = cp.CpSolver()
        sv.parameters.max_time_in_seconds = 120.0
        sv.parameters.num_search_workers = 8
        stt = sv.Solve(mdl)
        nm = sv.StatusName(stt)
        if stt not in (cp.OPTIMAL, cp.FEASIBLE):
            print(f"      CP {nm}: cannot even seat the residents we place"
                  f"   [{time.time()-t0:.0f}s]")
            continue
        got = int(sv.ObjectiveValue())
        print(f"      CP {nm}: seats all {len(res)} residents PLUS {got} of "
              f"{len(extra)} tardy blocks punctually   [{time.time()-t0:.0f}s]")
    eng.set_solution(st)
    eng.build_occ(hz)


if __name__ == "__main__":
    main()
