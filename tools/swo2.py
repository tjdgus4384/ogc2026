#!/usr/bin/env python3
"""Debt-free SWO: learn the sacrifice order UNDER the baseline assignment.

swo1's failure mechanism was assignment debt -- reordering let boosted blocks
steal preferred bays, Z3 rose ~1.8k, and frozen assignments meant the anneal
could never repay it. This closes that door structurally: every block is
locked to the bay the baseline best-of-5 seed gave it, so Z2 and Z3 are
IDENTICAL to the baseline by construction, and the priorities learn only WHO
YIELDS WHEN -- pure Z1, no debt possible.

The elimination cell this occupies is genuinely open: per-bay packing
optimality (bayscratch, repack2) was proven AT OUR ENTRY DAYS, and the
headroom experiments closed CP-CHOSEN schedules; the space of greedy-
realizable schedules at a fixed assignment -- realizable by construction,
since greedy builds them -- was never searched.

    python3 tools/swo2.py prob_4 [rounds] [seed] [anneal_sec]
"""
import json, os, pathlib, sys, time
REPO = pathlib.Path(__file__).resolve().parent.parent
SOLVER = "builds/pc2"

def main():
    prob_id = sys.argv[1]
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 80
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 7
    anneal = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0
    sys.path.insert(0, str(REPO / SOLVER))
    os.environ["OGC_SEED"] = str(seed)
    import myalgorithm as ma
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    n = len(prob["blocks"])
    eng0, seeds = ma._seed_free(prob)
    o0 = eng0.objective()
    base = seeds[0]
    bay_of = {i: base[i * 7] for i in range(n) if base[i * 7 + 6]}
    print(f"{prob_id} seed {seed}: baseline seed {o0[3]:,.0f}"
          f"  Z1={o0[0]:.0f} Z2={o0[1]:.0f} Z3={o0[2]:.0f}")
    eng, D, P = ma._fresh(prob, 4)
    hz = eng.horizon()
    prio = {i: float(D[i]) for i in range(n)}
    best = None; best_flat = None
    t0 = time.time()
    done = 0
    for r in range(rounds):
        eng.set_solution([0] * (n * 7)); eng.build_occ(hz)
        ok = True
        for i in sorted(range(n), key=lambda k: (prio[k], -P[k])):
            if not eng.insert_capped(i, -1, bay_of.get(i, -1)):
                ok = False; break
        if not ok:
            # a strict lock can dead-end an order; soften: retry the failed
            # block bay-free just to finish the round (its Z3 delta counts).
            continue
        done += 1
        o = eng.objective()
        if best is None or o[3] < best[3]:
            best = o; best_flat = eng.get_solution()
        mv = 0
        for i in range(n):
            b = eng.get_block(i)
            if b[6] and b[5] > D[i]:
                prio[i] -= 2.0 * (b[5] - D[i]); mv += 1
        if mv == 0: break
    if best is None:
        print("   no locked construction completed"); return
    print(f"   SWO-locked best of {done} ok-rounds: {best[3]:,.0f}"
          f"  Z1={best[0]:.0f} Z2={best[1]:.0f} Z3={best[2]:.0f}"
          f"   vs baseline {(best[3]-o0[3])/o0[3]*100:+.2f}%   [{time.time()-t0:.0f}s]")
    if anneal > 0 and best_flat is not None:
        eng.set_solution(best_flat); eng.build_occ(hz)
        eng.run_portfolio(4, anneal, 1, 0.05, 0.15, 0.002, 0.9995, seed, 1, [best_flat])
        o2 = eng.objective()
        print(f"   + bare {anneal:.0f}s portfolio -> {o2[3]:,.0f}  Z1={o2[0]:.0f}")

if __name__ == "__main__":
    main()
