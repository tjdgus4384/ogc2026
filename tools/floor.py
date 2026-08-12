#!/usr/bin/env python3
"""What would a guaranteed floor be worth?

Pooling every baseline draw ever gated (216 on P5), the shipped solver returns
`_trivial` on 2.3% of runs. Every one of them scores EXACTLY 1,321,314,754 with
Z1 = 99,034 -- 124x the median board, and enough on its own to drag the P5 mean
to 43.3M against a 10.6M median. The grading server runs each hidden instance
once, so a 2.3% per-run rate is a 17% chance of at least one catastrophe across
eight finals instances.

Reading the driver, the mechanism is not subtle. `_arm_free` spends the entire
budget, and only when it returns None -- its board failed the official checker
on a measure-zero exact-touch artifact -- does the ladder build a FRESH seed
and try to legalise it. That happens with ~6s left, a checker call on n=200
costs ~2s, and the ladder needs several. So it falls through to `_trivial`,
which schedules each bay serially and earns 99,034 tardy days.

Nothing in the run ever holds a board known to pass the checker. The fix is to
make one early and keep it, and the only question this probe has to answer is
what that floor is worth and what it costs:

    seed board objective     the floor we would return instead of 1.32e9
    checker verdict on it    does a bare seed pass? (it is sparse, so it
                             should -- the artifacts come from tight anneals)
    checker wall cost        the price of the guarantee, against a 600s budget

    python3 tools/floor.py SOLVERDIR prob_5 [seed]
"""
import json
import os
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent


def main():
    solver_dir, prob_id = sys.argv[1], sys.argv[2]
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 7
    sys.path.insert(0, str(REPO / solver_dir))
    os.environ["OGC_SEED"] = str(seed)
    import myalgorithm as ma
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    n = len(prob["blocks"])

    t0 = time.time()
    eng, _ = ma._seed_free(prob, diversify=False)
    t_seed = time.time() - t0
    if eng is None:
        print(f"{prob_id}: _seed_free returned None")
        return
    o = eng.objective()
    sol = ma._recs_to_sol(ma._extract(eng, n), eng.entry_ranks())
    t1 = time.time()
    res = ma._check(prob, sol)
    t_chk = time.time() - t1

    # the floor procedure itself: seed -> gate ladder, with real budget.
    # The bare seed is not always clean (P5 seed 7 carries one exact-touch
    # artifact), and the whole point of building the floor EARLY is that the
    # ladder then has the budget to nudge it away instead of six seconds.
    t3 = time.time()
    gsol, gres = ma._gate(prob, eng, n, time.time() + 20.0)
    t_gate = time.time() - t3

    t2 = time.time()
    triv = ma._trivial(prob)
    t_triv = time.time() - t2
    # score _trivial through the same engine so the numbers are comparable
    eng2, D, P = ma._fresh(prob, 4)
    st = [0] * (n * 7)
    for day, ops in triv["operations"].items():
        for op in ops:
            if op["type"] == "ENTRY":
                i = op["block_id"]
                st[i * 7:i * 7 + 7] = [op["bay_id"], op["orient_idx"], op["x"],
                                       op["y"], int(day), int(day) + P[i], 1]
    eng2.set_solution(st)
    eng2.build_occ(eng2.horizon())
    ot = eng2.objective()

    fe = res.get("feasible") if res else None
    nv = len(res.get("violations", [])) if res else -1
    print(f"{prob_id} (seed {seed}, n={n})")
    print(f"   seed board     {o[3]:>16,.0f}   Z1={o[0]:>7.0f}  built in {t_seed:5.1f}s")
    print(f"   checker on it  feasible={fe}  violations={nv}  cost {t_chk:5.2f}s")
    print(f"   _trivial       {ot[3]:>16,.0f}   Z1={ot[0]:>7.0f}  built in {t_triv:5.1f}s")
    ok = gsol is not None
    print(f"   FLOOR via gate {'AVAILABLE' if ok else 'unavailable':>16}"
          f"   ladder cost {t_gate:5.2f}s"
          + (f"   -> {ot[3]/max(1.0,o[3]):.0f}x better than _trivial" if ok else ""))


if __name__ == "__main__":
    main()
