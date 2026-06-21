"""cumul_master.py -- the cumulative (area-resource) master of the 3-option strategy.

Parallel cumulative machines (bays, capacity = area), free assignment, prism-area demands, minimize
w1*sum tardiness + w3*Z3 (Z2 omitted -> LB only looser by w2*minZ2, tiny; still a VALID lower bound).
This is a RELAXATION of the true problem (2D non-overlap -> area<=capacity), minimized over assignment
AND schedule, so BestObjectiveBound is a VALID GLOBAL LOWER BOUND on the true objective.
Also reports the best feasible plan (optimistic schedule to realize with 2D packing later).

Usage: python3 tools/cumul_master.py <inst> [tl]
"""
import sys, json, pathlib, math
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "solution" / "dev"))
from ortools.sat.python import cp_model
from shapely.geometry import Polygon
from shapely.ops import unary_union


def prism_area(blk):
    best = None
    for o in blk["shape"]:
        ps = [Polygon(l) for l in o["layers"] if len(l) >= 3]
        ps = [p if p.is_valid else p.buffer(0) for p in ps]
        if ps:
            a = unary_union(ps).area
            best = a if best is None else min(best, a)
    return best or 1.0


def fits(blk, bay):
    for o in blk["shape"]:
        xs = [v[0] for l in o["layers"] for v in l]; ys = [v[1] for l in o["layers"] for v in l]
        if math.ceil(max(xs) - min(xs)) <= bay["width"] and math.ceil(max(ys) - min(ys)) <= bay["height"]:
            return True
    return False


def solve_master(prob, tl):
    blocks = prob["blocks"]; bays = prob["bays"]; n = len(blocks); m = len(bays)
    w = prob["weights"]; w1, w3 = w["w1"], w["w3"]
    maxR = max(b["release_time"] for b in blocks); sumP = sum(b["processing_time"] for b in blocks)
    H = maxR + sumP + 1
    cm = cp_model.CpModel()
    per_bay_iv = [[] for _ in bays]; per_bay_dem = [[] for _ in bays]
    T = []; z3_terms = []
    for i, b in enumerate(blocks):
        a = max(1, int(prism_area(b)))
        s = cm.NewIntVar(b["release_time"], H, f"s{i}")
        e = cm.NewIntVar(b["release_time"] + b["processing_time"], H, f"e{i}")
        cm.Add(e == s + b["processing_time"])
        t = cm.NewIntVar(0, H, f"t{i}"); cm.Add(t >= e - b["due_date"]); T.append(t)
        smax = max(b["bay_preferences"]); pres = []
        for j, bay in enumerate(bays):
            if not fits(b, bay):
                continue
            p = cm.NewBoolVar(f"x{i}_{j}")
            iv = cm.NewOptionalIntervalVar(s, b["processing_time"], e, p, f"iv{i}_{j}")
            per_bay_iv[j].append(iv); per_bay_dem[j].append(a); pres.append((p, j))
            z3_terms.append((p, smax - b["bay_preferences"][j]))
        cm.AddExactlyOne([p for p, _ in pres])
    for j, bay in enumerate(bays):
        if per_bay_iv[j]:
            cm.AddCumulative(per_bay_iv[j], per_bay_dem[j], bay["width"] * bay["height"])
    obj = w1 * sum(T) + w3 * sum(coef * p for p, coef in z3_terms)
    cm.Minimize(obj)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = tl
    solver.parameters.num_search_workers = 4
    st = solver.Solve(cm)
    has = st in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    status = {cp_model.OPTIMAL: "OPT", cp_model.FEASIBLE: "FEAS"}.get(st, "NONE")
    return (solver.BestObjectiveBound(), solver.ObjectiveValue() if has else None, status,
            solver.WallTime())


if __name__ == "__main__":
    inst = sys.argv[1]; tl = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0
    prob = json.load(open(ROOT / "data" / "train" / f"{inst}.json"))
    n = len(prob["blocks"]); m = len(prob["bays"])
    lb, plan, status, wt = solve_master(prob, tl)
    bk = json.load(open(ROOT / "data" / "best_known.json")).get(inst, 0)
    print(f"{inst} n={n} m={m}: LB={lb:.0f}  plan_obj={plan}  status={status}  "
          f"({wt:.1f}s)  | best_known(true)={bk:.0f}  LB/best={lb/bk if bk else 0:.2f}")
