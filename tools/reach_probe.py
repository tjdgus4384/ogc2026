#!/usr/bin/env python3
"""Is the empty 40% of the yard reachable at all, or only ungenerated?

P6's most congested window runs at 58% polygon occupancy while 118 blocks queue
outside it. Either the free space admits placements the kernel's candidate
vocabulary never proposes (cornerSetsRB offers residual BOUNDING-BOX corners
only), or the crane rule genuinely forbids every position in it. This settles
which: for a tardy block, on each day it spent waiting, it brute-forces EVERY
integer (bay, orientation, x, y) and asks the kernel's own vp for a verdict --
no vocabulary in the loop at all.

    python3 tools/reach_probe.py SOLVERDIR prob_6 runs/base53-s41/prob_6.sol.json [nblocks]

Reports, per block: how many days had at least one legal position the solver did
not take, and how many positions there were. Anything above zero is search
reach, not physics.
"""
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent


def main():
    solver_dir, prob_id, sol_path = sys.argv[1], sys.argv[2], sys.argv[3]
    nblk = int(sys.argv[4]) if len(sys.argv) > 4 else 6
    sys.path.insert(0, str(REPO / solver_dir))
    import myalgorithm as ma
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    B, bays = prob["blocks"], prob["bays"]
    n = len(B)
    eng, D, P = ma._fresh(prob)
    ops = json.load(open(sol_path))["operations"]
    rec = {}
    for day, evs in ops.items():
        for e in evs:
            if e["type"] == "ENTRY":
                rec[e["block_id"]] = (e["bay_id"], e["orient_idx"], e["x"],
                                      e["y"], int(day))
    flat = []
    for i in range(n):
        if i in rec:
            bay, ori, x, y, ent = rec[i]
            flat += [bay, ori, x, y, ent, ent + P[i], 1]
        else:
            flat += [0, 0, 0, 0, 0, 0, 0]
    eng.set_solution(flat)
    eng.build_occ(eng.horizon())

    tardy = sorted(((rec[i][4] + P[i] - D[i], i) for i in rec
                    if rec[i][4] + P[i] > D[i]), reverse=True)[:nblk]
    print(f"{solver_dir} {prob_id}: brute-forcing {len(tardy)} worst tardy blocks")
    for late, i in tardy:
        bay0, ori0, x0, y0, ent = rec[i]
        R = int(prob["blocks"][i]["release_time"])
        eng.set_block(i, bay0, ori0, x0, y0, ent, ent + P[i], False)
        eng.finalize_load()
        eng.build_occ(eng.horizon())
        found = []
        for day in range(R, ent):
            hits = 0
            for j, bb in enumerate(bays):
                for oi in range(len(prob["blocks"][i]["shape"])):
                    for x in range(0, int(bb["width"]) + 1):
                        for y in range(0, int(bb["height"]) + 1):
                            eng.set_block(i, j, oi, x, y, day, day + P[i], True)
                            eng.finalize_load()
                            if eng.vp_ok(i):
                                hits += 1
                            eng.set_block(i, j, oi, x, y, day, day + P[i], False)
                            eng.finalize_load()
            if hits:
                found.append((day, hits))
        eng.set_block(i, bay0, ori0, x0, y0, ent, ent + P[i], True)
        eng.finalize_load()
        eng.build_occ(eng.horizon())
        if found:
            print(f"  blk {i:>3} late {late:>3}d, waited {ent - R:>3}d: "
                  f"{len(found)}/{ent - R} waiting days had a legal position "
                  f"(earliest day {found[0][0]}, {found[0][1]} positions) "
                  f"-> {ent - found[0][0]} days recoverable")
        else:
            print(f"  blk {i:>3} late {late:>3}d, waited {ent - R:>3}d: "
                  f"no legal position on any waiting day -- physics")


if __name__ == "__main__":
    main()
