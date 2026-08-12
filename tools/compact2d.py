#!/usr/bin/env python3
"""Is the missing half of spatial compaction worth building?

blocked.py answered the last open question in the Z1 chain: 72-83% of our tardy
DAYS are blocks for which, at the last day they could still have been punctual,
no free layer-0 bounding box existed anywhere in the yard -- while 4.5-24.7x
their own area sat free. The floor is fragmented, not full.

The kernel already knows this. `alns::spatialCompact` was adopted for exactly
that pathology and its own comment records it: "35-42% of bay area is free but
scattered inside bounding boxes... peak polygon utilisation 58-65% vs bbox
utilisation 103-105%". But it is one-dimensional --

    int nx = c.oc->slideLeftX(sn, i, oi, s.y[i], xlo, s.x[i] - 1);
    int ny = s.y[i];                      // y never moves

-- so free space consolidates along each row and rows never consolidate with
each other.

Before spending a rebuild on `slideDownY`, measure whether the axis has any
room, exactly, with the kernel's own crane semantics: `nudgeBlock(i, dx, dy)`
moves (x,y) and re-runs `vpConservative` for the block AND every neighbour
sharing its stay, reverting on failure. So a Python loop over nudges IS the
real compaction, just slower.

The measurement has to be indirect, because compaction cannot change the score
on its own: the objective is a function of (bay, entry) alone, and compaction
touches neither. Its entire value is in the insertions it makes possible
afterwards. So we count those:

    k0  punctual re-insertions available on the shipped board
    k1  ... after more of the SHIPPED compaction (x only)
    k2  ... after 2D compaction (down, then left, to fixpoint)

k2 > k1 ~ k0 is the axis paying. k2 == k1 kills it for the price of a probe.

    python3 tools/compact2d.py SOLVERDIR prob_5 TL [seed]
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
    seed = int(sys.argv[4]) if len(sys.argv) > 4 else 7
    sys.path.insert(0, str(REPO / solver_dir))
    os.environ["OGC_SEED"] = str(seed)
    import myalgorithm as ma
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    B = prob["blocks"]
    n = len(B)

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
    print(f"{prob_id}: board {base[3]:,.0f}  Z1={base[0]:.0f}"
          f"  {len(tardy)} tardy   (TL {tl:.0f}s, seed {seed})")

    def punctual_slots(state, label):
        """How many tardy blocks can be re-placed inside their punctual window?"""
        got, gain = 0, 0
        for i in tardy:
            part = list(state)
            part[i * 7 + 6] = 0
            eng.set_solution(part)
            eng.build_occ(hz)
            cap = max(int(B[i]["release_time"]), D[i] - P[i])
            if eng.insert_capped(i, cap, -1):
                b = eng.get_block(i)
                if b[5] <= D[i]:
                    got += 1
                    gain += state[i * 7 + 5] - D[i]
        eng.set_solution(state)
        eng.build_occ(hz)
        print(f"   {label:<34} {got:3d} of {len(tardy)} tardy blocks"
              f" become punctual   ({gain} days)")
        return got

    def compact(state, dirs, passes=8):
        eng.set_solution(state)
        eng.build_occ(hz)
        moved = 0
        for _ in range(passes):
            mv = 0
            order = sorted((i for i in range(n) if state[i * 7 + 6]),
                           key=lambda i: (eng.get_block(i)[3], eng.get_block(i)[2]))
            for i in order:
                for dx, dy in dirs:
                    while eng.nudge_block(i, dx, dy):
                        mv += 1
            moved += mv
            if not mv:
                break
        out = eng.get_solution()
        o = eng.objective()
        assert abs(o[3] - base[3]) < 1e-6, "compaction must not change the score"
        return list(out), moved

    k0 = punctual_slots(st, "shipped board")
    t0 = time.time()
    sx, mx = compact(st, [(-1, 0)])
    k1 = punctual_slots(sx, f"+ x-only compaction ({mx} nudges)")
    s2, m2 = compact(st, [(0, -1), (-1, 0)])
    k2 = punctual_slots(s2, f"+ 2D compaction ({m2} nudges)")
    print(f"   [{time.time()-t0:.0f}s]   y axis is worth "
          f"{'BUILDING' if k2 > k1 else 'nothing'}: k0={k0} k1={k1} k2={k2}")


if __name__ == "__main__":
    main()
