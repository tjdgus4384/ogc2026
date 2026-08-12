#!/usr/bin/env python3
"""Deterministic verification of the floor, by staging the catastrophe.

The failure this fixes fires on 2.3% of P5 runs, so a gate cannot verify it --
48 pairs would expect one event, and our sign test reads the median anyway.
It can be verified exactly instead, because the failure has two entry points
and both are reachable on demand:

  ladder path      `_arm_free` returns None (its board failed the checker) and
                   the fresh-seed ladder fails too -> today: `_trivial`
  exception path   anything under `_arm_free` raises -> today: `_trivial`

Staged here by letting `_gate` succeed exactly once -- for the floor -- and
fail forever after, which is precisely the state the five recorded
catastrophes were in. A pass means the run returns the floor instead of
1,321,314,754, and that the answer is a real board, not just a better number:
it is scored through the engine and re-checked against the official checker.

    python3 tools/floorcheck.py SOLVERDIR prob_5 [TL]
"""
import json
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent


def score(ma, prob, sol):
    n = len(prob["blocks"])
    eng, D, P = ma._fresh(prob, 4)
    st = [0] * (n * 7)
    for day, ops in sol["operations"].items():
        for op in ops:
            if op["type"] == "ENTRY":
                i = op["block_id"]
                st[i * 7:i * 7 + 7] = [op["bay_id"], op["orient_idx"], op["x"],
                                       op["y"], int(day), int(day) + P[i], 1]
    eng.set_solution(st)
    eng.build_occ(eng.horizon())
    return eng.objective()


def main():
    solver_dir, prob_id = sys.argv[1], sys.argv[2]
    tl = float(sys.argv[3]) if len(sys.argv) > 3 else 60.0
    sys.path.insert(0, str(REPO / solver_dir))
    import myalgorithm as ma
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{prob_id}.json"))
    print(f"{solver_dir} on {prob_id} @T{tl:.0f}")

    triv = score(ma, prob, ma._trivial(prob))[3]
    print(f"   _trivial scores {triv:,.0f}")

    ok = True
    # The third path is not a collapse at all: `_arm_free` comes back with a
    # board the checker ACCEPTS that is nonetheless worse than the floor. The
    # gate's own rescue ladder can produce exactly that -- its loose-relocation
    # stage may park a block arbitrarily late -- and no enumerated fallback
    # covers it. Only floor2's `_better` does.
    for label, mode in (("ladder path", "none"), ("exception path", "raise"),
                        ("worse-than-floor", "worse")):
        real_gate, real_arm = ma._gate, ma._arm_free
        state = {"n": 0}

        def gate(*a, **k):                    # clean once (the floor), then never
            state["n"] += 1
            if state["n"] == 1:
                return real_gate(*a, **k)
            return None, {"feasible": False, "violations": ["staged"]}

        def arm(*a, **k):
            if mode == "raise":
                raise RuntimeError("staged failure")
            if mode == "worse":
                bad = ma._trivial(prob)          # feasible by construction
                return bad, ma._check(prob, bad)
            return None, None

        ma._gate, ma._arm_free = gate, arm
        try:
            sol = ma.algorithm(prob, timelimit=tl)
        finally:
            ma._gate, ma._arm_free = real_gate, real_arm
        obj = score(ma, prob, sol)[3]
        res = ma._check(prob, sol)
        feas = res.get("feasible") if res else None
        good = obj < triv * 0.5 and feas is not False
        ok = ok and good
        print(f"   {label:<15} -> {obj:>16,.0f}  checker feasible={feas}"
              f"   {'PASS' if good else 'FAIL'}"
              f"   ({triv/max(1.0,obj):.0f}x better than _trivial)")
    print("   " + ("ALL PASS" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
