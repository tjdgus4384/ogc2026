"""Is a better ASSIGNMENT geometrically realizable at Z1=0?

On the 22 training instances where the engine already reaches Z1=0, the whole
objective is w2*Z2 + w3*Z3, which depends on the assignment alone (MODEL.md F1).
A trivial "everyone into their argmax-preference bay" assignment scores far
lower on 13 of them -- but only if the packer can still place everything on
time in the crowded bays that assignment creates.

The kernel already answers that: reconstruct_assigned(asg, tight=True) rebuilds
from empty with the bay assignment forced and entry capped at D-P, so success
means "this assignment admits a zero-tardiness packing". Returns -1 on success,
otherwise the id of the block it could not place.

KERNEL_SPEC secs 189-191 concluded this is capacity-blocked, but measured only
p22 and p28. This runs every Z1=0 instance.

Usage: python tools/probe_assign.py <prob_id>   -> one JSON line
"""
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "solver"))
import myalgorithm as MA   # noqa: E402


def z2z3(prob, asg):
    B, bays = prob["blocks"], prob["bays"]
    m = len(bays)
    A = [b["width"] * b["height"] for b in bays]
    avg = sum(A) / m
    u = [avg / a for a in A]
    load = [0.0] * m
    z3 = 0.0
    for i, b in enumerate(B):
        j = asg[i]
        load[j] += b["workload"]
        z3 += max(b["bay_preferences"]) - b["bay_preferences"][j]
    wl = [u[j] * load[j] for j in range(m)]
    return (math.floor(max(wl) - min(wl)) if m > 1 else 0), z3


def main():
    pid = sys.argv[1]
    prob = json.loads((ROOT / "data" / "train" / f"prob_{pid}.json").read_text())
    B, w = prob["blocks"], prob["weights"]
    m = len(prob["bays"])

    pref = [max(range(m), key=lambda j: b["bay_preferences"][j]) for b in B]
    z2, z3 = z2z3(prob, pref)
    target = w["w2"] * z2 + w["w3"] * z3

    rec = {"inst": f"prob_{pid}", "m": m, "n": len(B),
           "pref_z2": z2, "pref_z3": z3, "pref_cost_if_z1_zero": target}

    eng, _, _ = MA._fresh(prob)
    # tight=True forces entry <= D-P, i.e. zero tardiness by construction.
    r = eng.reconstruct_assigned(pref, True, 7)
    rec["tight_rc"] = r
    rec["tight_ok"] = (r == -1)
    if r == -1:
        o = eng.objective()
        rec["tight_obj"] = {"z1": o[0], "z2": o[1], "z3": o[2], "total": o[3]}
    else:
        # free rebuild: same assignment, tardiness allowed -- tells us the price
        eng2, _, _ = MA._fresh(prob)
        r2 = eng2.reconstruct_assigned(pref, False, 7)
        rec["free_rc"] = r2
        if r2 == -1:
            o = eng2.objective()
            rec["free_obj"] = {"z1": o[0], "z2": o[1], "z3": o[2], "total": o[3]}

    print(json.dumps(rec), flush=True)


if __name__ == "__main__":
    main()
