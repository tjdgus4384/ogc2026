"""Recombination plan -> targeted minimal repair (vp-free entry shifts,
checker-judged) -> generic gate fallback -> score.
Usage: mip_gate.py <pnum> <planfile>"""
import json
import math
import re
import sys
import time

sys.path.insert(0, "solver")
import myalgorithm as M
import utils

pnum = int(sys.argv[1])
plan = json.load(open(sys.argv[2]))
prob = json.load(open(f"data/preliminary_test/prob_{pnum}.json"))
n = len(prob["blocks"])
D = [int(round(b["due_date"])) for b in prob["blocks"]]
P = [max(1, int(math.ceil(b["processing_time"] - 1e-9)))
     for b in prob["blocks"]]
H = max(D) + max(P) + 40
eng = M._build_engine(prob)
eng.build_occ(H)
flat = []
for i in range(n):
    b_, x_, y_, o_, e_, f_ = plan[str(i)]
    flat += [b_, o_, x_, y_, e_, f_, 1]
eng.set_solution(flat)
eng.build_occ(H)

def recs_now():
    out = {}
    for i in range(n):
        g = eng.get_block(i)
        if g[6]:
            out[i] = [g[0], g[2], g[3], g[1], g[4], g[5]]
    return out

def check_now():
    return utils.check_feasibility(prob, M._recs_to_sol(recs_now()))

for _ in range(8):
    res = check_now()
    if res.get("feasible"):
        break
    nviol = len(res.get("violations", []))
    ids = [int(x) for x in
           re.findall(r"block (\d+)", res["violations"][0])]
    R = [int(math.ceil(b["release_time"] - 1e-9))
         for b in prob["blocks"]]
    def try_set(bid, g, de):
        if g[4] + de < R[bid] or g[5] + de > H:
            return None
        eng.set_block(bid, g[0], g[1], g[2], g[3],
                      g[4] + de, g[5] + de, True)
        eng.finalize_load()
        r2 = check_now()
        if r2.get("feasible") or len(r2.get("violations", [])) < nviol:
            return True
        eng.set_block(bid, g[0], g[1], g[2], g[3], g[4], g[5], True)
        eng.finalize_load()
        return False

    fixed = False
    for bid in ids:
        g = eng.get_block(bid)
        for de in (1, -1, 2, -2, 3, -3, 4, -4):
            if try_set(bid, g, de):
                fixed = True
                print(json.dumps({"shift": [bid, de]}), flush=True)
                break
        if fixed:
            break
    if not fixed and len(ids) >= 2:            # combined pair shifts
        a, b = ids[0], ids[1]
        ga, gb = eng.get_block(a), eng.get_block(b)
        for da in (1, -1, 2, -2):
            if ga[4] + da < 0 or ga[5] + da > H:
                continue
            eng.set_block(a, ga[0], ga[1], ga[2], ga[3],
                          ga[4] + da, ga[5] + da, True)
            eng.finalize_load()
            if try_set(b, gb, -da):
                fixed = True
                print(json.dumps({"pairshift": [a, da, b, -da]}),
                      flush=True)
                break
            eng.set_block(a, ga[0], ga[1], ga[2], ga[3],
                          ga[4], ga[5], True)
            eng.finalize_load()
    if not fixed:                              # relocate one block freely
        for bid in ids:
            g = eng.get_block(bid)
            eng.set_block(bid, g[0], g[1], g[2], g[3], g[4], g[5], False)
            eng.finalize_load()
            eng.build_occ(H)
            if eng.insert_capped(bid, H, -1):
                r2 = check_now()
                if r2.get("feasible") or                         len(r2.get("violations", [])) < nviol:
                    fixed = True
                    print(json.dumps({"reloc": bid}), flush=True)
                    break
            eng.set_block(bid, g[0], g[1], g[2], g[3], g[4], g[5], True)
            eng.finalize_load()
            eng.build_occ(H)
    if not fixed:
        print(json.dumps({"repair_stuck": ids, "viol": nviol}), flush=True)
        break

res = check_now()
if res.get("feasible"):
    sol = M._recs_to_sol(recs_now())
else:
    print(json.dumps({"unrepaired_final": len(res.get("violations", []))}))
    sys.exit(0)
print(json.dumps({"feasible": res.get("feasible"),
                  "objective": res.get("objective"),
                  "obj1": res.get("obj1"), "obj2": res.get("obj2"),
                  "obj3": res.get("obj3")}))
json.dump(sol, open(f"runs/mip_gated_p{pnum}.sol.json", "w"))
