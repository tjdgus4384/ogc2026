#!/usr/bin/env python3
"""Deterministic verification of the mid-run floor promotion (floor v2).

The staged scenario is the recorded residual catastrophe: the FINAL gate fails
on the annealed board. Staging: `_gate` succeeds for its first `succeed_n`
calls and fails forever after. v6.2 makes two gate calls on this path (the
start-of-run floor, then the final gate), so succeed_n=1 stages "final fails"
and the run must fall back to the seed-level floor -- the measured 20.1M on
P5. floor3 adds the mid-run promotion between them, so succeed_n=2 stages the
same "final fails" and the run must return the PROMOTED, annealed-level floor
instead. The verification is the ratio between those two returns.

    python3 tools/floorcheck3.py SOLVERDIR prob_5 TL SUCCEED_N
"""
import json, os, pathlib, sys, time
REPO = pathlib.Path(__file__).resolve().parent.parent

def main():
    d, pid = sys.argv[1], sys.argv[2]
    tl = float(sys.argv[3]); succ = int(sys.argv[4])
    sys.path.insert(0, str(REPO / d)); os.environ["OGC_SEED"] = "7"
    import myalgorithm as ma
    prob = json.load(open(REPO / "data" / "preliminary_test" / f"{pid}.json"))
    real_gate = ma._gate
    state = {"n": 0}
    def gate(*a, **k):
        state["n"] += 1
        if state["n"] <= succ:
            return real_gate(*a, **k)
        return None, {"feasible": False, "violations": ["staged"]}
    ma._gate = gate
    t0 = time.time()
    sol = ma.algorithm(prob, timelimit=tl)
    w = time.time() - t0
    ma._gate = real_gate
    res = ma._check(prob, sol)
    print("%-14s %s succeed_n=%d  returned %14s  feasible=%s  gates seen=%d  %5.1fs"
          % (d, pid, succ,
             format(res.get("objective", 0) if res else 0, ",.0f"),
             res.get("feasible") if res else None, state["n"], w))

if __name__ == "__main__":
    main()
