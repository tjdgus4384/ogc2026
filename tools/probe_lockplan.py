"""Ceiling probe for the locked plan-anneal (new attempt A).

Pipeline: budgeted-preference plan (v12seed's _plan_seed machinery, inlined
here against the solver base) -> insert_capped chase -> LOCK assignments to
the plan bays -> single-arm anneal for WALL seconds -> report Z1/Z2/Z3
against the unlocked baseline anneal from the same wall budget.

Usage: OGC_SOLVER=<dir> python probe_lockplan.py <pid> <wall> [putil]
"""
import json
import math
import os
import sys

sys.path.insert(0, os.environ["OGC_SOLVER"])
import myalgorithm as MA  # noqa: E402


def plan_and_chase(p, n, D, P, R, putil):
    B = p["blocks"]; bays = p["bays"]; m = len(bays)
    span = max(D) - min(R)
    budget = [by["width"] * by["height"] * span * putil for by in bays]
    area, fits = [], []
    for b in B:
        dims = []
        for o in b["shape"]:
            vs = [v for L in o["layers"] for v in L]
            xs = [v[0] for v in vs]; ys = [v[1] for v in vs]
            dims.append((max(xs) - min(xs), max(ys) - min(ys)))
        area.append(dims[0][0] * dims[0][1])
        fits.append([any(w <= by["width"] + 1e-9 and h <= by["height"] + 1e-9
                         for w, h in dims) for by in bays])
    pref = [b["bay_preferences"] for b in B]
    bay_of = [None] * n
    for i in sorted(range(n), key=lambda i: -area[i] * P[i]):
        for j in sorted(range(m), key=lambda j: -pref[i][j]):
            if fits[i][j] and budget[j] >= area[i] * P[i]:
                bay_of[i] = j
                budget[j] -= area[i] * P[i]
                break
        if bay_of[i] is None:
            return None, None
    horizon = max(D) + max(P) + 40
    cap = [by["width"] * by["height"] for by in bays]
    free = [[c] * (horizon + 1) for c in cap]
    plan_e = [None] * n
    for i in sorted(range(n), key=lambda i: (D[i], -P[i])):
        j = bay_of[i]; fj = free[j]
        got = None
        for e in range(R[i], horizon - P[i]):
            if all(fj[t] >= area[i] for t in range(e, e + P[i])):
                got = e
                break
        if got is None:
            return None, None
        for t in range(got, got + P[i]):
            fj[t] -= area[i]
        plan_e[i] = got
    eng, _, _ = MA._fresh(p)
    for i in sorted(range(n), key=lambda i: (plan_e[i], D[i])):
        if not (eng.insert_capped(i, plan_e[i], bay_of[i])
                or eng.insert_capped(i, -1, bay_of[i])):
            return None, None          # STRICT: assignment must hold
    return eng, bay_of


def main():
    pid, wall = sys.argv[1], float(sys.argv[2])
    putil = float(sys.argv[3]) if len(sys.argv) > 3 else 0.85
    data = os.environ.get("OGC_DATA", "preliminary_test")
    p = json.load(open(
        f"/data_fast/home/tjdgus4384/priv/ogc2026/data/{data}/prob_{pid}.json"))
    B = p["blocks"]; n = len(B)
    D = [int(round(b["due_date"])) for b in B]
    P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
    R = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]

    eng, bay_of = plan_and_chase(p, n, D, P, R, putil)
    if eng is None:
        print(json.dumps({"inst": pid, "error": "chase failed"}))
        return
    z = eng.objective()
    print(json.dumps({"inst": pid, "stage": "chased",
                      "z1": z[0], "z2": z[1], "z3": z[2], "obj": z[3]}))
    eng.set_assignment_lock(bay_of)
    r = eng.run_alns(wall, 0.05, 0.15, 0.002, 0.9995, 7, 0, 0.75, 0.06, 0x3FF)
    z = eng.objective()
    print(json.dumps({"inst": pid, "stage": "locked_anneal", "wall": wall,
                      "iters": int(r[0]), "z1": z[0], "z2": z[1], "z3": z[2],
                      "obj": z[3]}))

    # baseline: ordinary unlocked single-arm anneal, same wall
    eng2, _ = MA._seed_free(p, diversify=False)
    r2 = eng2.run_alns(wall, 0.05, 0.15, 0.002, 0.9995, 7, 0, 0.75, 0.06,
                       0x3FF)
    z2 = eng2.objective()
    print(json.dumps({"inst": pid, "stage": "baseline", "wall": wall,
                      "iters": int(r2[0]), "z1": z2[0], "z2": z2[1],
                      "z3": z2[2], "obj": z2[3]}))


if __name__ == "__main__":
    main()
