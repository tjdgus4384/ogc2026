#!/usr/bin/env python3
"""Does the assignment lock actually hold? Measured at each stage.

asgprobe locked the CP-optimal P3 assignment -- the one whose regret is Z3=24 --
and the run came back with Z3=524..593. Under a hard lock that is impossible:
`repair::insertGreedy` refuses any bay but the locked one

    if (c.forcedBay && c.forcedBay[A] >= 0 && c.forcedBay[A] != j) return false;
    if (c.forcedBay && c.forcedBay[A] >= 0) bays.assign(1, c.forcedBay[A]);

so either the lock is not reaching the code that places blocks, or it is being
dropped somewhere between the seed and the answer. There is a known place where
it was dropped before -- `_seed_free` replays the winning insertion order with
`[-1]*n`, an UNLOCKED reconstruction, and solver/VERSION records that exact bug
as repaired in v5.5 ("63 of 200 P3 blocks off their locked bay at the seed")
while the line is still in the file.

This counts, at each stage, how many blocks sit off their locked bay. Anything
above zero localises the leak; the count at the seed separates the replay from
the portfolio.

    python3 tools/lockhold.py SOLVERDIR prob_3 SECONDS
"""
import json
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))


def main():
    solver_dir, prob_id = sys.argv[1], sys.argv[2]
    secs = float(sys.argv[3]) if len(sys.argv) > 3 else 20.0
    sys.path.insert(0, str(REPO / solver_dir))
    os.environ.setdefault("OGC_SEED", "7")
    import myalgorithm as ma
    from asgprobe import solve_asg
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    n = len(prob["blocks"])

    mode = sys.argv[4] if len(sys.argv) > 4 else "cp"
    if mode == "prod":
        # exactly the driver's own partial lock (_arm_free): top 40% by
        # preference spread, pinned to their argmax bay. This is what actually
        # ships on P3, so this is the number that matters.
        pref = [b["bay_preferences"] for b in prob["blocks"]]
        spread = sorted(range(n), key=lambda i: -(max(pref[i]) - min(pref[i])))
        A = [-1] * n
        for i in spread[:int(n * 0.4)]:
            A[i] = max(range(len(pref[i])), key=lambda j: pref[i][j])
        print(f"{prob_id}: PRODUCTION partial lock, {sum(1 for a in A if a>=0)}"
              f" of {n} blocks pinned")
    else:
        A, obj = solve_asg(prob, 30.0, None)
        print(f"{prob_id}: CP assignment, geometry-free {obj:,.0f}")

    def off(flat, label):
        locked = sum(1 for i in range(n) if A[i] >= 0)
        bad = sum(1 for i in range(n)
                  if flat[i * 7 + 6] and A[i] >= 0 and flat[i * 7] != A[i])
        unpl = sum(1 for i in range(n) if not flat[i * 7 + 6])
        print(f"   {label:<36} off-lock {bad:3d}/{locked}   unplaced {unpl:3d}")
        return bad

    eng, seeds = ma._seed_free(prob, asg=A)
    if eng is None:
        print("   _seed_free returned None under the full lock")
        return
    off(list(eng.get_solution()), "engine state after _seed_free")
    for k, s in enumerate(seeds):
        off(list(s), f"starts[{k}] (what the portfolio gets)")

    eng.set_assignment_lock(A)
    starts = [seeds[0], []] + seeds[1:3]
    eng.run_portfolio(4, secs, 1, 0.05, 0.15, 0.002, 0.9995, 7, 1, starts)
    eng.set_assignment_lock([])
    o = eng.objective()
    off(list(eng.get_solution()), f"after {secs:.0f}s portfolio")
    print(f"   objective {o[3]:,.0f}   Z1={o[0]:.0f} Z2={o[1]:.0f} Z3={o[2]:.0f}"
          f"   (locked assignment scores Z3=24)")


if __name__ == "__main__":
    main()
