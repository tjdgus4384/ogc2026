#!/usr/bin/env python3
"""Would an EXACT repair beat the greedy one on the same hole?

terminal.py measured what happens at convergence: from the annealed board, 400
random destroy-repair moves at 5%, 15% and 30% produced zero improvements, and
at 30% not even a tie -- greedy refills a large hole strictly worse than what
was there. So the incumbent is already better than anything greedy can build,
and the neighbourhood the search samples is exhausted.

That leaves one thing untested about this neighbourhood: the repair. If the
same hole were refilled OPTIMALLY rather than first-fit, would it improve? The
answer splits the remaining work cleanly:

  CP beats greedy   the repair operator is the bottleneck, and the lever is to
                    scale exact repair up (the shipped slice arm runs it at
                    K=25 with the rest frozen);
  CP also fails     the neighbourhood is exhausted regardless of repair
                    quality, and only a structurally different move can help.

The candidate set is the kernel's own (feasible_positions), so this measures the
exact optimum OVER THAT VOCABULARY -- which is the same vocabulary the shipped
CP arm uses, and the honest scope of any claim it makes.

    python3 tools/exactrepair.py SOLVERDIR prob_4 TL TRIALS [seed]
"""
import json
import os
import pathlib
import random
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent


def main():
    solver_dir, prob_id = sys.argv[1], sys.argv[2]
    tl = float(sys.argv[3])
    trials = int(sys.argv[4])
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
    base = eng.objective()[3]
    placed = [i for i in range(n) if st[i * 7 + 6]]
    rng = random.Random(seed)
    print(f"{prob_id}: shipped pipeline board {base:,.0f} (TL {tl:.0f}s, seed {seed})")

    for frac in (0.15, 0.30):
        q = max(2, int(frac * n))
        g_better = c_better = c_solved = 0
        g_best = c_best = 0.0
        t0 = time.time()
        for _ in range(trials):
            victims = rng.sample(placed, min(q, len(placed)))
            part = list(st)
            for i in victims:
                part[i * 7 + 6] = 0

            # --- greedy repair, exactly as alns::run does it
            eng.set_solution(part)
            eng.build_occ(hz)
            ok = all(eng.insert_capped(i, -1, -1)
                     for i in sorted(victims, key=lambda k: -P[k]))
            if ok:
                v = eng.objective()[3]
                if v < base - 1e-9:
                    g_better += 1
                    g_best = min(g_best, (v - base) / base * 100)

            # --- exact repair over the kernel's own candidate vocabulary
            eng.set_solution(part)
            eng.build_occ(hz)
            CAND = {}
            for i in victims:
                raw = eng.feasible_positions(i, 30, 0)
                cs = [tuple(raw[k:k + 5]) for k in range(0, len(raw), 5)]
                c0 = tuple(st[i * 7:i * 7 + 5])
                if c0 not in cs:
                    cs.append(c0)              # the incumbent stays available
                CAND[i] = cs
            mdl = cp.CpModel()
            V, flat, idx = {}, [], []
            for i in victims:
                V[i] = [mdl.NewBoolVar(f"z{i}_{k}") for k in range(len(CAND[i]))]
                mdl.AddExactlyOne(V[i])
                for k, (j, oi, x, y, e) in enumerate(CAND[i]):
                    flat += [i, j, oi, x, y, e]
                    idx.append((i, k))
            prs = eng.pair_conflicts(flat)
            for t in range(0, len(prs), 2):
                (i, k), (i2, k2) = idx[prs[t]], idx[prs[t + 1]]
                mdl.AddBoolOr([V[i][k].Not(), V[i2][k2].Not()])
            mdl.Minimize(sum((W["w1"] * max(0, c[4] + P[i] - D[i])
                              + W["w3"] * (SMAX[i] - PREF[i][c[0]])) * V[i][k]
                             for i in victims for k, c in enumerate(CAND[i])))
            sv = cp.CpSolver()
            sv.parameters.max_time_in_seconds = 10.0
            sv.parameters.num_search_workers = 4
            status = sv.Solve(mdl)
            if status in (cp.OPTIMAL, cp.FEASIBLE):
                new = list(part)
                for i in victims:
                    for k, c in enumerate(CAND[i]):
                        if sv.Value(V[i][k]):
                            new[i * 7:i * 7 + 7] = [c[0], c[1], c[2], c[3],
                                                    c[4], c[4] + P[i], 1]
                            break
                eng.set_solution(new)
                eng.build_occ(hz)
                if not any(new[i * 7 + 6] and not eng.vp_ok(i) for i in range(n)):
                    c_solved += 1
                    v = eng.objective()[3]
                    if v < base - 1e-9:
                        c_better += 1
                        c_best = min(c_best, (v - base) / base * 100)
        el = time.time() - t0
        print(f"   destroy {frac*100:3.0f}% (q={q:3d}) x{trials}:"
              f"  greedy improves {g_better:3d} (best {g_best:+.2f}%)"
              f"   exact improves {c_better:3d} of {c_solved:3d} solved"
              f" (best {c_best:+.2f}%)   [{el:.0f}s]")
    eng.set_solution(st)
    eng.build_occ(hz)


if __name__ == "__main__":
    main()
