"""P3 assignment frontier: between the unpackable argmax (Z3=0, June) and
the anneal's fixed point (Z3~500) lies unexplored ground. For each area
budget util u: CP-assign minimizing Z3 -> lock -> EDD chase -> short locked
anneal. If some packable assignment reaches Z1=0 with Z3 ~<= 350, P3 beats
rank-1 (70.9k).

Usage: OGC_SOLVER=<dir> python probe_p3frontier.py <pid> <anneal_s> u1 u2 ...
"""
import json
import math
import os
import sys

sys.path.insert(0, os.environ["OGC_SOLVER"])
import myalgorithm as MA  # noqa: E402
import z3bound            # noqa: E402  (tools/ on sys.path via cwd)


def main():
    pid = sys.argv[1]
    anneal_s = float(sys.argv[2])
    utils = [float(u) for u in sys.argv[3:]]
    data = os.environ.get("OGC_DATA", "preliminary_test")
    p = json.load(open(
        f"/data_fast/home/tjdgus4384/priv/ogc2026/data/{data}/prob_{pid}.json"))
    B = p["blocks"]; n = len(B)
    D = [int(round(b["due_date"])) for b in B]

    for u in utils:
        st, z3lb = z3bound.bound(p, u, "/tmp/asg.json")
        if z3lb is None:
            print(json.dumps({"util": u, "status": st}))
            continue
        asg = json.load(open("/tmp/asg.json"))
        eng, _, _ = MA._fresh(p)
        strict = 0
        for i in sorted(range(n), key=lambda i: D[i]):
            if eng.insert_capped(i, -1, asg[i]):
                strict += 1
            else:
                strict = -1
                break
        if strict < 0:
            print(json.dumps({"util": u, "z3_assign": z3lb,
                              "chase": "failed"}))
            continue
        eng.set_assignment_lock(asg)
        eng.run_alns(anneal_s, 0.05, 0.15, 0.002, 0.9995, 7, 0, 0.75, 0.06,
                     0x3FF)
        z = eng.objective()
        print(json.dumps({"util": u, "z3_assign": z3lb, "chased": strict,
                          "z1": z[0], "z2": z[1], "z3": z[2], "obj": z[3]}))


if __name__ == "__main__":
    main()
