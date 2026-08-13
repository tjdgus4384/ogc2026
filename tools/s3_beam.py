#!/usr/bin/env python3
"""S3: sequential rollout-guided construction, debt-free by design.

S2 passed: at 8/8 (P4), 7/8 (P6), 1/8 (P5) sacrifice points, single-decision
variants evaluated by rollout beat the greedy construction, with edges up to
-18.7% -- and crucially the DEFER variants (pure order, zero assignment debt,
F3-safe) carry edges on their own. S3 iterates the mechanism: walk the
construction; at each undecided sacrifice (first tardy block), evaluate defer
offsets {0, +5, +15} by rollout-to-completion; fix the best; repeat until no
undecided sacrifice remains. Every decision is judged by its simulated future
-- the mechanism no existing operator has.

Then the standard ladder: constructed board vs the best-of-5 seed, and the
bare-400s plateau test on request.

    python3 tools/s3_beam.py prob_4 [seed] [anneal_sec]
"""
import json, os, pathlib, sys, time
REPO = pathlib.Path(__file__).resolve().parent.parent
SOLVER = "builds/pc2"

def main():
    prob_id = sys.argv[1]
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 7
    anneal = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
    sys.path.insert(0, str(REPO / SOLVER))
    os.environ["OGC_SEED"] = str(seed)
    import myalgorithm as ma
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    n = len(prob["blocks"])
    eng0, seeds = ma._seed_free(prob)
    o0 = eng0.objective()
    print(f"{prob_id} seed {seed}: best-of-5 seed {o0[3]:,.0f}  Z1={o0[0]:.0f}")

    eng, D, P = ma._fresh(prob, 4)
    hz = eng.horizon()
    base_order = sorted(range(n), key=lambda i: (D[i], -P[i]))
    offset = {}                       # block -> defer offset (decided)

    def build(extra=None):
        """construct with offsets (+extra override); returns (obj, tardy_seq)."""
        offs = dict(offset)
        if extra:
            offs.update(extra)
        seq = []
        for k, i in enumerate(base_order):
            seq.append((k + offs.get(i, 0), k, i))
        seq.sort()
        seq = [i for _, _, i in seq]
        # deterministic: reconstruct_ordered is a pure function of the order
        # (the S2 lesson -- insert_capped draws made the loop non-monotone)
        if eng.reconstruct_ordered([-1] * n, seq, False, 7) != -1:
            return None, None
        tardy = [i for i in seq if eng.get_block(i)[6]
                 and eng.get_block(i)[5] > D[i]]
        return eng.objective(), tardy

    t0 = time.time()
    cur, tardy = build()
    decided = set()
    rolls = 0
    while time.time() - t0 < 150.0:
        nxt = next((i for i in tardy if i not in decided), None)
        if nxt is None:
            break
        decided.add(nxt)
        cand = {0: cur}
        for d in (5, 15, 40):
            o, _ = build({nxt: offset.get(nxt, 0) + d})
            rolls += 1
            if o is not None:
                cand[d] = o
        bestd = min(cand, key=lambda d: cand[d][3])
        if bestd != 0:
            offset[nxt] = offset.get(nxt, 0) + bestd
        cur, tardy = build()
    print(f"   S3: {cur[3]:,.0f}  Z1={cur[0]:.0f} Z2={cur[1]:.0f}"
          f" Z3={cur[2]:.0f}   ({len(offset)} defers fixed, {rolls} rollouts)"
          f"   vs seed {(cur[3]-o0[3])/o0[3]*100:+.2f}%   [{time.time()-t0:.0f}s]")
    if anneal > 0:
        flat = eng.get_solution()
        eng.run_portfolio(4, anneal, 1, 0.05, 0.15, 0.002, 0.9995, seed, 1, [flat])
        o2 = eng.objective()
        print(f"   + bare {anneal:.0f}s portfolio -> {o2[3]:,.0f}  Z1={o2[0]:.0f}")

if __name__ == "__main__":
    main()
