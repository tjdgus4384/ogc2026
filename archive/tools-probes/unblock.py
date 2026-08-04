"""Tardy-unblock driver: per-bay mini-CPs with targeted dense vocab.

For each bay holding tardy blocks (incumbent board), free the tardy
blocks plus every same-bay resident whose stay overlaps a target's
[R_i, e_i) waiting window, give exactly those blocks a scoped dense
grid (XGEN5), solve with FREESEL=list, gate, feed if better.
Usage: unblock.py <pnum> <solve_sec_per_round>
"""
import json
import math
import os
import subprocess
import sys

pnum = int(sys.argv[1])
tl = sys.argv[2] if len(sys.argv) > 2 else "600"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
PY = sys.executable

prob = json.load(open(f"data/preliminary_test/prob_{pnum}.json"))
B = prob["blocks"]
n = len(B)
P = [max(1, int(math.ceil(b["processing_time"] - 1e-9))) for b in B]
R = [int(math.ceil(b["release_time"] - 1e-9)) for b in B]
D = [int(round(b["due_date"])) for b in B]


def board_state():
    best = json.load(open(f"runs/mipboards/prob_{pnum}.sol.json"))
    ent, bay = {}, {}
    for t, ops in best["operations"].items():
        for op in ops:
            if op["type"] == "ENTRY":
                ent[op["block_id"]] = int(t)
                bay[op["block_id"]] = op["bay_id"]
    return ent, bay


for rnd in range(3):
    ent, bay = board_state()
    tardy = [i for i in range(n) if i in ent and ent[i] + P[i] > D[i]]
    groups = {}
    for i in tardy:
        groups.setdefault(bay[i], []).append(i)
    if not groups:
        print(json.dumps({"round": rnd, "tardy": 0}))
        break
    print(json.dumps({"round": rnd, "tardy": len(tardy),
                      "bays": sorted(groups)}), flush=True)
    improved = False
    for tb, targets in sorted(groups.items()):
        free = set(targets)
        for i in targets:
            for j in range(n):
                if j in ent and bay.get(j) == tb and \
                        ent[j] < ent[i] and ent[j] + P[j] > R[i]:
                    free.add(j)
        env = dict(os.environ)
        env.update(MENU="4", XFER="2", XGEN4="12", XGEN4T="12",
                   DENSELIST=",".join(map(str, sorted(free))),
                   DENSEBAYS="" if os.environ.get("DENSEALL") else str(tb),
                   DSTRIDE=os.environ.get("DSTRIDE", "2"),
                   XGEN5=os.environ.get("XGEN5", "40"))
        print(json.dumps({"bay": tb, "free": len(free)}), flush=True)
        subprocess.run([PY, "tools/compat_pairs.py", str(pnum)],
                       env=env, check=True,
                       stdout=subprocess.DEVNULL)
        env2 = dict(os.environ)
        env2.update(FREESEL="list",
                    FREELIST=",".join(map(str, sorted(free))))
        subprocess.run([PY, "tools/mip_v3.py", str(pnum), tl],
                       env=env2, check=True)
        subprocess.run([PY, "tools/mip_gate.py", str(pnum),
                        f"runs/mip_v3_p{pnum}.json"], check=True)
        sys.path.insert(0, "solver")
        import utils
        new = json.load(open(f"runs/mip_gated_p{pnum}.sol.json"))
        rn = utils.check_feasibility(prob, new)
        cur = json.load(open(f"runs/mipboards/prob_{pnum}.sol.json"))
        rc = utils.check_feasibility(prob, cur)
        if rn["feasible"] and rn["objective"] < rc["objective"]:
            json.dump(new,
                      open(f"runs/mipboards/prob_{pnum}.sol.json", "w"))
            print(json.dumps({"fed": rn["objective"],
                              "was": rc["objective"]}), flush=True)
            improved = True
        else:
            print(json.dumps({"kept": rc["objective"]}), flush=True)
    if not improved:
        print(json.dumps({"converged_round": rnd}))
        break
