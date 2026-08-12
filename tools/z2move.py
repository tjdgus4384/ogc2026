#!/usr/bin/env python3
"""Is the imbalance term reducible on a finished board?

Z2 is the one objective term that does not depend on geometry or on time at
all: it is max over bay pairs of |u_j * (total workload in bay j) - u_k * ...|.
It is decided purely by which bay each block is assigned to.

And nothing in the kernel steers it. insertGreedy orders bays by PREFERENCE,
op 8 chases preference, op 9 chases tardiness, and the CP slice arm minimises
w1*tardiness + w3*preference with no Z2 term at all. Z2 appears only in the
acceptance test, after the fact.

On P3 that is not a detail. Z1 is zero there, the objective is 79,160, and
w2*Z2 = 16,310 -- 20.6% of it, and LARGER than the 15,040 gap to the all-team
minimum. Perfect balance on that board would put u_j*L_j at 5,420 in every bay;
our worst pair differs by 3,262, a 60% imbalance.

Earlier probes only ever moved blocks for PREFERENCE (cycle_probe found the 22
blocks that want another bay and measured 273 punctual relocations, none of
which improved). Moving a block because the bay is OVERLOADED is a different
move set and has never been tried.

This enumerates it exactly: on the shipped board, every block in the
heaviest bay, tried in every other bay, kept only if it lands punctually, and
scored on the true objective.

    python3 tools/z2move.py SOLVERDIR prob_3 TL [seed]
"""
import json
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent


def main():
    solver_dir, prob_id = sys.argv[1], sys.argv[2]
    tl = float(sys.argv[3])
    seed = int(sys.argv[4]) if len(sys.argv) > 4 else 7
    sys.path.insert(0, str(REPO / solver_dir))
    os.environ["OGC_SEED"] = str(seed)
    import myalgorithm as ma
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    B, bays = prob["blocks"], prob["bays"]
    n, m = len(B), len(bays)
    W = prob["weights"]
    areas = [b["width"] * b["height"] for b in bays]
    u = [sum(areas) / m / a for a in areas]
    wl = [b["workload"] for b in B]
    PREF = [[float(v) for v in b["bay_preferences"]] for b in B]

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
    load = [0.0] * m
    for i in range(n):
        if st[i * 7 + 6]:
            load[st[i * 7]] += wl[i]
    norm = [u[j] * load[j] for j in range(m)]
    hi = max(range(m), key=lambda j: norm[j])
    lo = min(range(m), key=lambda j: norm[j])
    print(f"{prob_id}: board {base[3]:,.0f}  Z1={base[0]:.0f} Z2={base[1]:.0f} "
          f"Z3={base[2]:.0f}   (w2*Z2 = {W['w2']*base[1]:,.0f}, "
          f"{W['w2']*base[1]/base[3]*100:.1f}% of the objective)")
    print(f"   u*load per bay: {[round(v) for v in norm]}"
          f"   perfect balance would be {round(sum(norm)/m)}")
    print(f"   heaviest bay {hi}, lightest {lo}, gap {norm[hi]-norm[lo]:.0f}")

    movers = [i for i in range(n) if st[i * 7 + 6] and st[i * 7] == hi]
    print(f"   {len(movers)} blocks sit in the heaviest bay; trying each in every other bay")
    best = (0.0, None)
    tried = punctual = 0
    for i in movers:
        for j in range(m):
            if j == hi:
                continue
            tried += 1
            part = list(st)
            part[i * 7 + 6] = 0
            eng.set_solution(part)
            eng.build_occ(hz)
            cap = max(int(B[i]["release_time"]), D[i] - P[i])
            if not eng.insert_capped(i, cap, j):
                continue
            punctual += 1
            v = eng.objective()
            d = (v[3] - base[3]) / base[3] * 100
            if v[3] < base[3] - 1e-9 and d < best[0]:
                best = (d, (i, j, v))
    print(f"   {tried} moves tried, {punctual} landed punctually")
    if best[1]:
        i, j, v = best[1]
        print(f"   BEST balance move: block {i} -> bay {j}: {v[3]:,.0f} ({best[0]:+.2f}%)")
    else:
        print("   no single punctual relocation out of the heaviest bay improves")

    # THE MIRROR. Balance moves lose because preference is worth 150 a point
    # while a median block shifts Z2 by about 151 units = 755. The same
    # arithmetic says a PREFERENCE move should pay 3.8x: average regret among
    # the blocks that have any is ~19 points = 2,850 against the same ~755.
    # cycle_probe tried this and found nothing, but on a 90s single-arm board
    # scoring 99,730 -- not the board the pipeline actually ships.
    reg = [(max(PREF[i]) - PREF[i][st[i*7]], i) for i in range(n) if st[i*7+6]]
    reg = sorted([r for r in reg if r[0] > 0], reverse=True)
    print(f"   {len(reg)} blocks carry preference regret"
          f" (total {sum(r for r, _ in reg):.0f}, worst {reg[0][0] if reg else 0:.0f})")
    best2 = (0.0, None)
    tried2 = punc2 = 0
    for r, i in reg:
        here = st[i * 7]
        for j in range(m):
            if j == here or PREF[i][j] <= PREF[i][here]:
                continue
            tried2 += 1
            part = list(st)
            part[i * 7 + 6] = 0
            eng.set_solution(part)
            eng.build_occ(hz)
            cap = max(int(B[i]["release_time"]), D[i] - P[i])
            if not eng.insert_capped(i, cap, j):
                continue
            punc2 += 1
            v = eng.objective()
            d = (v[3] - base[3]) / base[3] * 100
            if v[3] < base[3] - 1e-9 and d < best2[0]:
                best2 = (d, (i, j, v))
    print(f"   preference moves: {tried2} tried, {punc2} landed punctually")
    if best2[1]:
        i, j, v = best2[1]
        print(f"   BEST preference move: block {i} -> bay {j}: {v[3]:,.0f}"
              f" ({best2[0]:+.2f}%)   Z1={v[0]:.0f} Z2={v[1]:.0f} Z3={v[2]:.0f}")
    else:
        print("   no single punctual preference relocation improves either")
    eng.set_solution(st)
    eng.build_occ(hz)


if __name__ == "__main__":
    main()
