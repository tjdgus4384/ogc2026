#!/usr/bin/env python3
"""Do same-day, same-bay ENTRY groups ever exceed kBatchCap?

`Engine::entryRanks` groups every placed block by (bay, entry day) and, for
groups larger than core::kBatchCap = 16, gives up on verification:

    if (m > core::kBatchCap) {            // rule 2b never admits these
        for (int r = 0; r < m; r++) rank[g[r]] = r;   // ascending id, unchecked
        continue;
    }

The comment's premise is that rule 2b rejects such boards, and rule 2b does
carry `if (nb2 > kBatchCap) return false`. But those two are not the same set.
vpConservative first tries `entryOK` with the WHOLE same-day batch present and
CLEARS the batch if that passes, so its `nb2` counts only the blocks that
actually obstruct. A (bay, day) group of twenty whose obstructing subset is
three passes the oracle and then gets an ascending-id order nobody checked.

That order is what `_recs_to_sol` emits, so if it violates the sub-order rule 2b
proved, the board fails the official checker at Stage 5 -- which is the path
that ends in `_trivial` and the 124x catastrophes v6.2's floor now absorbs.

This just counts. Deterministic, no lottery: run the shipped pipeline and
histogram the group sizes.

    python3 tools/batchcap.py SOLVERDIR prob_5 TL [seed]
"""
import collections, json, os, pathlib, sys
REPO = pathlib.Path(__file__).resolve().parent.parent

def main():
    d, pid = sys.argv[1], sys.argv[2]
    tl = float(sys.argv[3]); seed = int(sys.argv[4]) if len(sys.argv) > 4 else 7
    sys.path.insert(0, str(REPO / d)); os.environ["OGC_SEED"] = str(seed)
    import myalgorithm as ma
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{pid}.json"))
    n = len(prob["blocks"])
    sol = ma.algorithm(prob, timelimit=tl)
    eng, D, P = ma._fresh(prob, 4)
    st = [0]*(n*7)
    for day, ops in sol["operations"].items():
        for op in ops:
            if op["type"] == "ENTRY":
                i = op["block_id"]
                st[i*7:i*7+7] = [op["bay_id"], op["orient_idx"], op["x"], op["y"],
                                 int(day), int(day)+P[i], 1]
    eng.set_solution(st); eng.build_occ(eng.horizon())
    g = collections.Counter()
    for i in range(n):
        if st[i*7+6]: g[(st[i*7], st[i*7+4])] += 1
    sizes = collections.Counter(g.values())
    over = [k for k, v in g.items() if v > 16]
    print(f"{pid} seed {seed}: board {eng.objective()[3]:,.0f}, "
          f"{len(g)} (bay,day) entry groups")
    print("   size histogram: " + " ".join(f"{s}x{c}" for s, c in sorted(sizes.items())))
    print(f"   groups over kBatchCap(16): {len(over)}"
          + (f"   -> {over[:6]} sizes {[g[k] for k in over[:6]]}" if over else
             "   (entryRanks always verifies here)"))

if __name__ == "__main__":
    main()
