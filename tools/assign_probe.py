#!/usr/bin/env python3
"""How many preference-improving bay relocations are geometrically available?

Z3 (preference regret) is 38-59% of the gap to the all-team minimum on P4/P5/P6
and essentially all of it on P3, and Z3 is a function of the ASSIGNMENT ALONE.
Yet `_assign_descent` reports `moves: 0` on every board and seed measured: it
ranks candidates exactly, then every move_block(tight) fails geometrically.

move_block(i, j, tight) removes block i and re-inserts it in bay j at an entry
no later than D_i - P_i, so it needs a punctual slot on a congested day in the
target bay -- precisely what the same-day ENTRY rule governs. This probe counts,
on a finished board, how many such relocations each kernel admits, so the Z3
axis can be priced against the oracle rather than argued about.

    python3 tools/assign_probe.py builds/b53 prob_4 runs/base53-s41/prob_4.sol.json
"""
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent


def main():
    solver_dir, prob_id, sol_path = sys.argv[1], sys.argv[2], sys.argv[3]
    sys.path.insert(0, str(REPO / solver_dir))
    import myalgorithm as ma
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    B = prob["blocks"]
    n = len(B)
    eng, D, P = ma._fresh(prob)
    ops = json.load(open(sol_path))["operations"]
    rec = {}
    for day, evs in ops.items():
        for e in evs:
            if e["type"] == "ENTRY":
                rec[e["block_id"]] = (e["bay_id"], e["orient_idx"],
                                      e["x"], e["y"], int(day))
    flat = []
    for i in range(n):
        if i in rec:
            bay, ori, x, y, ent = rec[i]
            flat += [bay, ori, x, y, ent, ent + P[i], 1]
        else:
            flat += [0, 0, 0, 0, 0, 0, 0]
    eng.set_solution(flat)
    eng.build_occ(eng.horizon())

    pref = [b["bay_preferences"] for b in B]
    D = [int(round(b["due_date"])) for b in B]
    R = [int(b["release_time"]) for b in B]
    base = eng.objective()[3]
    cands = feasible = improving = late_only = no_room = 0
    best = 0.0
    gains = []
    detail = []
    for i in range(n):
        if i not in rec:
            continue
        cur = rec[i][0]
        better = [j for j in range(len(prob["bays"])) if pref[i][j] > pref[i][cur]]
        if not better:
            continue
        better.sort(key=lambda j: -pref[i][j])
        for j in better:
            cands += 1
            snap = eng.get_solution()
            ok = eng.move_block(i, j, True, 7)
            why, ent = "", None
            if ok:
                feasible += 1
                delta = eng.objective()[3] - base
                if delta < -1e-9:
                    improving += 1
                    gains.append(-delta)
                    best = max(best, -delta)
            else:
                # Separate "the bay has no room for it at all" from "no room
                # inside its punctual window [R, D-P]" -- only the first is a
                # packing limit; the second is a scheduling one, and the two
                # call for completely different mechanisms.
                # Mark it unplaced BEFORE rebuilding the occupancy grid --
                # build_occ stamps whatever is placed, so rebuilding first
                # leaves the block's own footprint in the grid and it blocks
                # itself, which reads as "the bay is full until much later".
                eng.set_solution(snap)
                eng.set_block(i, *snap[i * 7:i * 7 + 6], False)
                eng.finalize_load()
                eng.build_occ(eng.horizon())
                if eng.insert_capped(i, -1, j):
                    ent = eng.get_block(i)[4]
                    why = "late_only"
                    late_only += 1
                else:
                    why = "no_room"
                    no_room += 1
            eng.set_solution(snap)
            eng.build_occ(eng.horizon())
            if why:
                detail.append((pref[i][j] - pref[i][cur], i, cur, j, R[i],
                               D[i] - int(prob["blocks"][i]["processing_time"]),
                               why, ent))
    print(f"{solver_dir} {prob_id}: base obj {base:,.0f}")
    print(f"  preference-improving relocations considered {cands}, "
          f"feasible {feasible}, objective-improving {improving}")
    print(f"  failures: no room in that bay at any date {no_room}, "
          f"room only OUTSIDE the punctual window {late_only}")
    if gains:
        gains.sort(reverse=True)
        print(f"  best single move -{best:,.0f}; top-10 sum -{sum(gains[:10]):,.0f}")
    detail.sort(reverse=True)
    for d, i, cur, j, r, latest, why, ent in detail[:6]:
        print(f"    blk {i:>3} regret {d:>4.0f} bay {cur}->{j} punctual window "
              f"[{r},{latest}] : {why}"
              + (f" (earliest possible entry {ent})" if ent is not None else ""))


if __name__ == "__main__":
    main()
