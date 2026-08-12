#!/usr/bin/env python3
"""Does pairConflicts flag the incumbent board against itself?

bayscratch freed one bay, seeded every block's OWN placement as a candidate, and
CP still returned INFEASIBLE on all three bays that finished. The incumbent
placements coexist on the shipped board by construction, so a model that cannot
select them is over-constrained.

This isolates it: feed pair_conflicts nothing but the incumbent placement of
every block, and count how many pairs it forbids. Any count above zero is the
model rejecting a board the checker accepts -- and the shipped slice arm builds
its model the same way, so every CP round there starts by ruling out the very
solution it is supposed to improve on.

    python3 tools/incumbent_conflict.py SOLVERDIR prob_5 TL [seed]
"""
import json, os, pathlib, sys
REPO = pathlib.Path(__file__).resolve().parent.parent

def main():
    solver_dir, prob_id, tl = sys.argv[1], sys.argv[2], float(sys.argv[3])
    seed = int(sys.argv[4]) if len(sys.argv) > 4 else 7
    sys.path.insert(0, str(REPO / solver_dir))
    os.environ["OGC_SEED"] = str(seed)
    import myalgorithm as ma
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    n = len(prob["blocks"])
    sol = ma.algorithm(prob, timelimit=tl)
    eng, D, P = ma._fresh(prob, 4)
    st = [0] * (n * 7)
    for day, ops in sol["operations"].items():
        for op in ops:
            i = op["block_id"]
            if op["type"] == "ENTRY":
                st[i*7:i*7+7] = [op["bay_id"], op["orient_idx"], op["x"], op["y"],
                                 int(day), int(day) + P[i], 1]
    eng.set_solution(st); eng.build_occ(eng.horizon())
    print(f"{prob_id}: board {eng.objective()[3]:,.0f}, "
          f"vp violations {sum(1 for i in range(n) if st[i*7+6] and not eng.vp_ok(i))}")
    placed = [i for i in range(n) if st[i*7+6]]
    flat, idx = [], []
    for i in placed:
        flat += [i, st[i*7], st[i*7+1], st[i*7+2], st[i*7+3], st[i*7+4]]
        idx.append(i)
    prs = eng.pair_conflicts(flat)
    print(f"   {len(placed)} incumbent placements -> {len(prs)//2} conflict pairs "
          f"flagged by the model")
    for t in range(0, min(len(prs), 20), 2):
        a, b = idx[prs[t]], idx[prs[t+1]]
        print(f"      blk {a} (bay {st[a*7]}, e={st[a*7+4]}, f={st[a*7+5]})  vs  "
              f"blk {b} (bay {st[b*7]}, e={st[b*7+4]}, f={st[b*7+5]})"
              f"   same e: {st[a*7+4]==st[b*7+4]}, same f: {st[a*7+5]==st[b*7+5]}")

if __name__ == "__main__":
    main()
