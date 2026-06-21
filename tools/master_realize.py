"""master_realize.py -- complete the 3-option strategy end-to-end and MEASURE it.

Solve the cumulative (area) master EXACTLY-ish (CP-SAT), take its ASSIGNMENT (x_ij), then REALIZE in 2D:
pack each bay with the real footprint-disjoint packer (entry times re-derived, since the area-plan's times
are too optimistic to 2D-realize). Compute the TRUE objective via utils. Compare to best-known.

Tests whether the area-optimal ASSIGNMENT (the master's structural output) is good for the real 2D problem.
"""
import sys, json, pathlib, math
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "solution" / "baseline" / "baseline"))
sys.path.insert(0, str(ROOT / "solution" / "dev"))
sys.path.insert(0, str(ROOT / "tools"))
from ortools.sat.python import cp_model
import utils, solve
from cumul_master import prism_area, fits


def master_assignment(prob, tl):
    blocks = prob["blocks"]; bays = prob["bays"]
    w = prob["weights"]; w1, w3 = w["w1"], w["w3"]
    maxR = max(b["release_time"] for b in blocks); sumP = sum(b["processing_time"] for b in blocks)
    H = maxR + sumP + 1
    cm = cp_model.CpModel()
    iv = [[] for _ in bays]; dem = [[] for _ in bays]; T = []; z3 = []; pres_of = {}
    for i, b in enumerate(blocks):
        a = max(1, int(prism_area(b)))
        s = cm.NewIntVar(b["release_time"], H, f"s{i}"); e = cm.NewIntVar(b["release_time"]+b["processing_time"], H, f"e{i}")
        cm.Add(e == s + b["processing_time"])
        t = cm.NewIntVar(0, H, f"t{i}"); cm.Add(t >= e - b["due_date"]); T.append(t)
        smax = max(b["bay_preferences"]); ps = []
        for j, bay in enumerate(bays):
            if not fits(b, bay):
                continue
            p = cm.NewBoolVar(f"x{i}_{j}")
            iv[j].append(cm.NewOptionalIntervalVar(s, b["processing_time"], e, p, f"iv{i}_{j}"))
            dem[j].append(a); ps.append((p, j)); z3.append((p, smax - b["bay_preferences"][j]))
        cm.AddExactlyOne([p for p, _ in ps]); pres_of[i] = ps
    for j, bay in enumerate(bays):
        if iv[j]:
            cm.AddCumulative(iv[j], dem[j], bay["width"] * bay["height"])
    cm.Minimize(w1 * sum(T) + w3 * sum(c * p for p, c in z3))
    solver = cp_model.CpSolver(); solver.parameters.max_time_in_seconds = tl; solver.parameters.num_search_workers = 4
    st = solver.Solve(cm)
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    assign = {}
    for i, ps in pres_of.items():
        for p, j in ps:
            if solver.Value(p) == 1:
                assign[i] = j; break
    return assign


if __name__ == "__main__":
    inst = sys.argv[1]; tl = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0
    prob = json.load(open(ROOT / "data" / "train" / f"{inst}.json"))
    assign = master_assignment(prob, tl)
    metas = [solve.block_meta(prob["blocks"][i], i) for i in range(len(prob["blocks"]))]
    bk = json.load(open(ROOT / "data" / "best_known.json")).get(inst, 0)
    # realize: master assignment -> 2D pack
    sol = solve.build_ops(solve._pack_all(prob, metas, assign))
    r = utils.check_feasibility(prob, sol)
    # baseline: our own heuristic best assignment + same packer
    sol2 = solve.build_ops(solve._pack_all(prob, metas, solve._pick_assignment(prob, metas)))
    r2 = utils.check_feasibility(prob, sol2)
    o = r["objective"] if r["feasible"] else -1
    o2 = r2["objective"] if r2["feasible"] else -1
    print(f"{inst}: master-assign+pack={o:.0f}  our-assign+pack={o2:.0f}  best-known={bk:.0f}")
