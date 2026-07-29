"""Can the geometric packer realise the CP area-relaxation plan?

Inserts blocks in plan-entry order, each capped at its plan entry day.
Three passes, each relaxing one constraint:
  1 strict  : plan bay forced + entry <= plan day
  2 anybay  : entry <= plan day, bay free
  3 free    : uncapped (stragglers placed wherever/whenever possible)
Reports realisation counts and the final Z1. High strict/anybay realisation
=> plan-guided repair is a live lever; low => geometry cuts are dense.

Usage: OGC_SOLVER=<dir> python plan_pack.py <prob.json> <plan.json>
"""
import json
import os
import sys

sys.path.insert(0, os.environ["OGC_SOLVER"])
import myalgorithm as MA  # noqa: E402


def main():
    prob = json.load(open(sys.argv[1]))
    plan = json.load(open(sys.argv[2]))
    n = len(prob["blocks"])
    eng, D, P = MA._fresh(prob)
    order = sorted(range(n), key=lambda i: (plan[i]["entry"], D[i]))
    strict, anybay, free, failed = [], [], [], []
    for i in order:
        if eng.insert_capped(i, plan[i]["entry"], plan[i]["bay"]):
            strict.append(i)
        elif eng.insert_capped(i, plan[i]["entry"], -1):
            anybay.append(i)
        else:
            failed.append(i)
    for i in list(failed):
        if eng.insert_capped(i, -1, -1):
            free.append(i)
            failed.remove(i)
    sol = eng.get_solution()
    z1 = sum(max(0, sol[i * 7 + 5] - D[i]) for i in range(n)
             if sol[i * 7 + 6])
    print(json.dumps({
        "n": n,
        "strict": len(strict), "anybay": len(anybay),
        "late_free": len(free), "unplaced": len(failed),
        "z1_after": z1,
    }))


if __name__ == "__main__":
    main()
